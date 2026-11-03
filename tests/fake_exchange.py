"""Servidor HTTP real que imita los endpoints públicos de Binance spot que usa el operador paper:
/api/v3/klines, /api/v3/depth, /api/v3/exchangeInfo y /api/v3/ticker/24hr, más las velas de Coinbase
(respaldo del etiquetador) para que ningún test salga a internet.

El precio de cada par es una función del minuto (`price(sym, minuto_ms) -> apertura`), y el libro se arma
alrededor del precio del minuto actual del reloj del test.
"""
from decimal import ROUND_CEILING, ROUND_FLOOR
from decimal import Decimal as D

from aiohttp import web

MIN = 60_000

FILTERS = {   # como los informa Binance (strings con ceros a la derecha)
    "BTCUSDT": ("0.01000000", "0.00001000", "0.00001000", "5.00000000"),
    "ETHUSDT": ("0.01000000", "0.00010000", "0.00010000", "5.00000000"),
    "SOLUSDT": ("0.01000000", "0.00100000", "0.00100000", "5.00000000"),
}


def candle(price, sym: str, m: int) -> tuple[float, float, float, float]:
    o, c = price(sym, m), price(sym, m + MIN)
    return o, max(o, c) * 1.001, min(o, c) * 0.999, c


class FakeBinance:
    def __init__(self, price, clock, *, spread_bps: float = 2.0, level_qty: float = 1000.0):
        self.price, self.clock = price, clock
        self.spread_bps, self.level_qty = spread_bps, level_qty
        self.overrides: dict[tuple[str, int], tuple[float, float, float, float]] = {}   # (par, minuto): OHLC
        self.down: set[str] = set()                 # endpoints caídos: klines | depth | info | ticker
        self.last: dict[str, float] = {}            # último precio operado distinto del medio (divergencia)
        self.adv = 5e8                              # volumen de 24 h en USDT
        self.requests: list[tuple[str, str]] = []

    def kline(self, sym: str, m: int) -> tuple[float, float, float, float]:
        return self.overrides.get((sym, m)) or candle(self.price, sym, m)

    def now_price(self, sym: str) -> float:
        m = int(self.clock.time() * 1000) // MIN * MIN
        return self.price(sym, m)

    async def klines(self, request):
        q = request.query
        sym, start, end, limit = q["symbol"], int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        self.requests.append(("klines", sym))
        if "klines" in self.down:
            return web.json_response({"code": 0, "msg": "down"}, status=503)
        if sym not in FILTERS:
            return web.json_response({"code": -1121, "msg": "Invalid symbol."}, status=400)
        now_m = int(self.clock.time() * 1000) // MIN * MIN
        rows = []
        m = start
        while m <= min(end, now_m) and len(rows) < limit:   # nunca velas del futuro del reloj del test
            o, h, lo, c = self.kline(sym, m)
            rows.append([m, str(o), str(h), str(lo), str(c), "100", m + MIN - 1])
            m += MIN
        return web.json_response(rows)

    async def depth(self, request):
        sym = request.query["symbol"]
        self.requests.append(("depth", sym))
        if "depth" in self.down:
            return web.json_response({"code": 0, "msg": "down"}, status=503)
        if sym not in FILTERS:
            return web.json_response({"code": -1121, "msg": "Invalid symbol."}, status=400)
        mid, tick = D(str(self.now_price(sym))), D(FILTERS[sym][0])
        half = mid * D(str(self.spread_bps)) / 2 / 10_000
        bid = ((mid - half) / tick).to_integral_value(ROUND_FLOOR) * tick
        ask = max(((mid + half) / tick).to_integral_value(ROUND_CEILING) * tick, bid + tick)
        bids = [[str(bid - i * tick), str(self.level_qty)] for i in range(10)]
        asks = [[str(ask + i * tick), str(self.level_qty)] for i in range(10)]
        return web.json_response({"lastUpdateId": 1, "bids": bids, "asks": asks})

    async def exchange_info(self, request):
        sym = request.query["symbol"]
        self.requests.append(("info", sym))
        if "info" in self.down:
            return web.json_response({"code": 0, "msg": "down"}, status=503)
        if sym not in FILTERS:
            return web.json_response({"code": -1121, "msg": "Invalid symbol."}, status=400)
        tick, step, min_qty, min_notional = FILTERS[sym]
        return web.json_response({"symbols": [{
            "symbol": sym, "status": "TRADING", "baseAsset": sym.removesuffix("USDT"), "quoteAsset": "USDT",
            "filters": [{"filterType": "PRICE_FILTER", "minPrice": tick, "maxPrice": "1000000", "tickSize": tick},
                        {"filterType": "LOT_SIZE", "minQty": min_qty, "maxQty": "9000", "stepSize": step},
                        {"filterType": "NOTIONAL", "minNotional": min_notional, "applyMinToMarket": True}]}]})

    async def ticker(self, request):
        sym = request.query["symbol"]
        self.requests.append(("ticker", sym))
        if "ticker" in self.down:
            return web.json_response({"code": 0, "msg": "down"}, status=503)
        last = self.last.get(sym, self.now_price(sym))
        return web.json_response({"symbol": sym, "lastPrice": str(last), "quoteVolume": str(self.adv)})

    async def coinbase(self, request):
        return web.json_response({"message": "NotFound"}, status=404)

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_get("/api/v3/klines", self.klines)
        a.router.add_get("/api/v3/depth", self.depth)
        a.router.add_get("/api/v3/exchangeInfo", self.exchange_info)
        a.router.add_get("/api/v3/ticker/24hr", self.ticker)
        a.router.add_get("/products/{product}/candles", self.coinbase)
        return a

    @staticmethod
    def urls(base: str) -> dict:
        return {"depth": base + "/api/v3/depth", "info": base + "/api/v3/exchangeInfo",
                "ticker": base + "/api/v3/ticker/24hr"}

    @staticmethod
    def candle_urls(base: str) -> dict:
        return {"binance": base + "/api/v3/klines", "coinbase": base + "/products/{product}/candles"}
