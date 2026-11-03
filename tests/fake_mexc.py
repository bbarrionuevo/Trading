"""Servidor HTTP real que imita la API de contratos de MEXC (pública y privada de lectura), con el formato
{"success": true, "code": 0, "data": ...}. El precio es una función del minuto, como en fake_exchange.py."""
import hashlib
import hmac
from decimal import ROUND_CEILING, ROUND_FLOOR
from decimal import Decimal as D

from aiohttp import web

MIN = 60_000
ACCESS, SECRET = "mx0vglTESTACCESSKEY0001", "TESTSECRETKEY000000000000000000001"
SPECS = {   # contractSize, priceUnit, taker, maker, mmr
    "BTC_USDT": ("0.0001", "0.1", 0.0002, 0.0, 0.004),
    "ETH_USDT": ("0.01", "0.01", 0.0002, 0.0, 0.004),
    "SOL_USDT": ("1", "0.01", 0.0002, 0.0, 0.005),
    "TESLA_USDT": ("0.01", "0.01", 0.0002, 0.0, 0.01),          # una acción: Tesla (TSLA), como en MEXC
}
TRADFI = {"TESLA_USDT": ("TESLA", "TSLA")}                      # baseCoin, baseCoinName (el ticker de la acción)


def ok(data):
    return web.json_response({"success": True, "code": 0, "data": data})


class FakeMexc:
    def __init__(self, price, clock, *, spread_bps: float = 2.0, level_contracts: float = 5000.0):
        self.price, self.clock, self.spread_bps, self.level = price, clock, spread_bps, level_contracts
        self.funding = {"rate": 0.0001, "cycle_h": 8}
        self.account_fees = {"makerFee": 0.0002, "takerFee": 0.0006}
        self.requests: list[tuple[str, str]] = []
        self.signed: list[dict] = []
        self.down: set[str] = set()
        self.overrides: dict[tuple[str, int], tuple[float, float, float, float]] = {}   # (par, minuto): OHLC

    def now_m(self) -> int:
        return int(self.clock.time() * 1000) // MIN * MIN

    def _detail(self, sym: str) -> dict:
        size, unit, taker, maker, mmr = SPECS[sym]
        base, name = TRADFI.get(sym, (sym.split("_")[0], sym.split("_")[0]))
        return {"symbol": sym, "baseCoin": base, "baseCoinName": name, "quoteCoin": "USDT", "settleCoin": "USDT",
                "contractSize": float(size), "priceUnit": float(unit), "volUnit": 1, "minVol": 1, "maxVol": 1e6,
                "takerFeeRate": taker, "makerFeeRate": maker, "maintenanceMarginRate": mmr, "maxLeverage": 200,
                "state": 0, "apiAllowed": True, "type": 2 if sym in TRADFI else 1,
                "conceptPlate": ["mc-trade-zone-Stock"] if sym in TRADFI else []}

    async def detail(self, request):
        sym = request.query.get("symbol")
        self.requests.append(("detail", sym or "todos"))
        if "detail" in self.down:
            return web.json_response({"success": False, "code": 1001, "message": "contract not exists"})
        if sym is None:                                         # el catálogo entero (universo operable)
            return ok([self._detail(s) for s in SPECS])
        if sym not in SPECS:
            return web.json_response({"success": False, "code": 1001, "message": "contract not exists"})
        return ok(self._detail(sym))

    async def depth(self, request):
        sym = request.match_info["symbol"]
        self.requests.append(("depth", sym))
        if "depth" in self.down:
            return web.Response(status=503)
        size, unit, *_ = SPECS[sym]
        mid, tick = D(str(self.price(sym, self.now_m()))), D(unit)
        half = mid * D(str(self.spread_bps)) / 2 / 10_000
        bid = ((mid - half) / tick).to_integral_value(ROUND_FLOOR) * tick
        ask = max(((mid + half) / tick).to_integral_value(ROUND_CEILING) * tick, bid + tick)
        return ok({"bids": [[float(bid - i * tick), self.level, 3] for i in range(20)],
                   "asks": [[float(ask + i * tick), self.level, 3] for i in range(20)],
                   "version": 1, "timestamp": int(self.clock.time() * 1000)})

    async def kline(self, request):
        sym = request.match_info["symbol"]
        start, end = int(request.query["start"]) * 1000, int(request.query["end"]) * 1000
        self.requests.append(("kline", sym))
        if "kline" in self.down:
            return web.Response(status=503)
        out = {k: [] for k in ("time", "open", "close", "high", "low", "vol", "amount")}
        m = start
        while m <= min(end, self.now_m()):
            o, c = self.price(sym, m), self.price(sym, m + MIN)
            h, lo = max(o, c) * 1.001, min(o, c) * 0.999
            if (sym, m) in self.overrides:
                o, h, lo, c = self.overrides[(sym, m)]
            for k, v in (("time", m // 1000), ("open", o), ("close", c), ("high", h), ("low", lo), ("vol", 100.0),
                         ("amount", 100.0 * o)):
                out[k].append(v)
            m += MIN
        return ok(out)

    def _ticker(self, sym: str) -> dict:
        p = self.price(sym, self.now_m())
        return {"symbol": sym, "lastPrice": p, "fairPrice": p, "amount24": 2e9, "fundingRate": self.funding["rate"]}

    async def ticker(self, request):
        sym = request.query.get("symbol")
        self.requests.append(("ticker", sym or "todos"))
        if sym is None:                                         # todos los tickers (los que tienen precio en el test)
            out = []
            for s in SPECS:
                try:
                    out.append(self._ticker(s))
                except KeyError:
                    continue
            return ok(out)
        return ok(self._ticker(sym))

    async def funding_rate(self, request):
        sym = request.match_info["symbol"]
        self.requests.append(("funding", sym))
        cycle = self.funding["cycle_h"] * 3600_000
        nxt = (int(self.clock.time() * 1000) // cycle + 1) * cycle
        return ok({"symbol": sym, "fundingRate": self.funding["rate"], "collectCycle": self.funding["cycle_h"],
                   "nextSettleTime": nxt, "timestamp": int(self.clock.time() * 1000)})

    def _check_signature(self, request) -> bool:
        key, t, sig = request.headers.get("ApiKey"), request.headers.get("Request-Time"), request.headers.get("Signature")
        params = "&".join(f"{k}={v}" for k, v in sorted(request.query.items()))
        want = hmac.new(SECRET.encode(), f"{ACCESS}{t}{params}".encode(), hashlib.sha256).hexdigest()
        self.signed.append({"path": request.path, "method": request.method, "ok": key == ACCESS and sig == want})
        return key == ACCESS and sig == want

    async def private(self, request):
        if not self._check_signature(request):
            return web.json_response({"success": False, "code": 602, "message": "Signature verification failed"})
        if request.path.endswith("tiered_fee_rate"):
            return ok({"level": 0, **self.account_fees})
        return ok([{"currency": "USDT", "availableBalance": 0, "equity": 0}])

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_get("/api/v1/contract/detail", self.detail)
        a.router.add_get("/api/v1/contract/depth/{symbol}", self.depth)
        a.router.add_get("/api/v1/contract/kline/{symbol}", self.kline)
        a.router.add_get("/api/v1/contract/ticker", self.ticker)
        a.router.add_get("/api/v1/contract/funding_rate/{symbol}", self.funding_rate)
        a.router.add_route("*", "/api/v1/private/{tail:.*}", self.private)
        return a
