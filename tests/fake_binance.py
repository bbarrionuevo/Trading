"""Binance Futures USDⓈ-M falso para el modo real: verifica la firma HMAC de la query, valida órdenes (prueba sin
ejecutar), rechaza la "solo maker" (GTX) que cruzaría, lleva órdenes y posiciones, y programa fallas (límite 429,
modo multi-activo, cobertura, bloqueo por reglas cuantitativas, otro lado).

También sirve los datos públicos de mercado (catálogo con cripto y TradFi, libro, velas de 1 minuto, volumen,
precio de marca y funding): el precio es una función del minuto, como en fake_mexc.py."""
import hashlib
import hmac
import time
from decimal import ROUND_CEILING, ROUND_FLOOR
from decimal import Decimal as D
from urllib.parse import urlencode

from aiohttp import web

KEY, SECRET = "clave-binance-de-prueba", "secreto-binance-de-prueba"
MIN = 60_000
# tick, paso, mínimo, nominal mínimo, tipo de contrato, subyacente, activo base
SPECS = {
    "XRPUSDT": ("0.0001", "0.1", "0.1", "5", "PERPETUAL", "COIN", "XRP"),
    "BTCUSDT": ("0.1", "0.001", "0.001", "50", "PERPETUAL", "COIN", "BTC"),
    "ETHUSDT": ("0.01", "0.001", "0.001", "20", "PERPETUAL", "COIN", "ETH"),
    "SOLUSDT": ("0.01", "0.01", "0.01", "5", "PERPETUAL", "COIN", "SOL"),
    "1000PEPEUSDT": ("0.0000001", "1", "1", "5", "PERPETUAL", "COIN", "1000PEPE"),
    "SMALLUSDT": ("0.0001", "1", "1", "5", "PERPETUAL", "COIN", "SMALL"),
    "TINYUSDT": ("0.0001", "1", "1", "5", "PERPETUAL", "COIN", "TINY"),
    "TSLAUSDT": ("0.01", "0.01", "0.01", "5", "TRADIFI_PERPETUAL", "EQUITY", "TSLA"),
    "XAUUSDT": ("0.01", "0.001", "0.001", "5", "TRADIFI_PERPETUAL", "COMMODITY", "XAU"),
    "XAGUSDT": ("0.001", "0.01", "0.01", "5", "TRADIFI_PERPETUAL", "COMMODITY", "XAG"),
    "SPYUSDT": ("0.01", "0.01", "0.01", "5", "TRADIFI_PERPETUAL", "EQUITY", "SPY"),
    "OPENAIUSDT": ("0.01", "0.01", "0.01", "5", "TRADIFI_PERPETUAL", "PREMARKET", "OPENAI"),
    "BTCDOMUSDT": ("0.1", "0.001", "0.001", "5", "PERPETUAL", "INDEX", "BTCDOM"),
}
PRICES = {"BTCUSDT": 100_000.0, "ETHUSDT": 4_000.0, "SOLUSDT": 150.0, "1000PEPEUSDT": 0.005, "SMALLUSDT": 1.0,
          "TINYUSDT": 1.0, "TSLAUSDT": 400.0, "XAUUSDT": 4_000.0, "XAGUSDT": 50.0, "SPYUSDT": 650.0,
          "OPENAIUSDT": 100.0, "BTCDOMUSDT": 6_000.0}
VOLUMES = {"SMALLUSDT": 500_000.0, "TINYUSDT": 50_000.0}          # el resto, 2.000 M por día


class FakeBinance:
    def __init__(self, bid="0.5000", ask="0.5001", *, price=None, clock=None, spread_bps: float = 2.0,
                 level: float = 1_000.0):
        self.bid, self.ask = D(bid), D(ask)
        self.price = price or (lambda sym, m: PRICES.get(sym))     # (contrato, minuto en ms) → precio
        self.clock, self.spread_bps, self.level = clock, spread_bps, level
        self.funding_rate = 0.0001
        self.overrides: dict[tuple[str, int], tuple[float, float, float, float]] = {}   # (par, minuto): OHLC
        self.balance = D("50")
        self.dual = False
        self.multi_assets = False
        self.locked = False
        self.throttle = 0                         # cuántos pedidos responder con 429
        self.swap_sides = False                   # el exchange "entiende" el otro lado
        self.orders: dict[str, dict] = {}
        self.tests: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.signed: list[bool] = []
        self.cancel_after: list[float] = []       # segundos entre crear y cancelar (reglas cuantitativas)
        self.margin, self.leverage = {}, {}
        self.algos: dict[str, dict] = {}         # stops del servicio Algo
        self.algo_lag = 0                         # consultas que siguen diciendo NEW tras cancelar (el real tarda)
        self.algo_stuck = False                   # el stop no se cancela de a uno (solo con "cancelar todos")
        self.fail_cancel_all = False              # "cancelar todas las órdenes" da error

    def _check(self, request) -> bool:
        q = dict(request.query)
        sig = q.pop("signature", None)
        raw = request.query_string.split("&signature=")[0]
        good = request.headers.get("X-MBX-APIKEY") == KEY and sig == hmac.new(
            SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest() and "timestamp" in q
        self.signed.append(good)
        return good

    async def handle(self, request):
        path, m = request.path, request.method
        self.calls.append((m, path))
        if self.throttle:
            self.throttle -= 1
            return web.json_response({"code": -1003, "msg": "Too many requests"}, status=429,
                                     headers={"Retry-After": "30"})
        if path == "/fapi/v1/time":
            return web.json_response({"serverTime": int(time.time() * 1000)})
        if path == "/fapi/v1/exchangeInfo":
            return web.json_response({"symbols": [self._spec(sym) for sym in SPECS]})
        if path in ("/fapi/v1/depth", "/fapi/v1/klines", "/fapi/v1/ticker/24hr", "/fapi/v1/premiumIndex",
                    "/fapi/v1/fundingInfo"):
            return self._public(path, dict(request.query))
        if path == "/fapi/v1/ticker/bookTicker":
            return web.json_response({"symbol": "XRPUSDT", "bidPrice": str(self.bid), "askPrice": str(self.ask)})
        if not self._check(request):
            return web.json_response({"code": -1022, "msg": "Signature for this request is not valid."}, status=400)
        q = dict(request.query)
        if path == "/fapi/v2/balance":
            return web.json_response([{"asset": "USDT", "balance": str(self.balance), "crossUnPnl": "0",
                                       "availableBalance": str(self.balance)}])
        if path == "/fapi/v1/apiTradingStatus":
            ind = {"XRPUSDT": [{"isLocked": True, "indicator": "UFR"}]} if self.locked else {}
            return web.json_response({"indicators": ind})
        if path == "/fapi/v1/positionSide/dual":
            return web.json_response({"dualSidePosition": self.dual})
        if path == "/fapi/v1/multiAssetsMargin":
            return web.json_response({"multiAssetsMargin": self.multi_assets})
        if path == "/fapi/v2/positionRisk":
            return web.json_response([])
        if path == "/fapi/v1/openOrders":
            return web.json_response([o for o in self.orders.values() if o["status"] == "NEW"])
        if path == "/fapi/v1/marginType":
            if self.margin.get(q["symbol"]) == q["marginType"]:
                return web.json_response({"code": -4046, "msg": "No need to change margin type."}, status=400)
            self.margin[q["symbol"]] = q["marginType"]
            return web.json_response({"code": 200, "msg": "success"})
        if path == "/fapi/v1/leverage":
            self.leverage[q["symbol"]] = int(q["leverage"])
            return web.json_response({"symbol": q["symbol"], "leverage": int(q["leverage"])})
        if path in ("/fapi/v1/order", "/fapi/v1/order/test") and m == "POST" and q.get("type") != "LIMIT":
            return web.json_response({"code": -4120, "msg": "Order type not supported for this endpoint. Please use "
                                      "the Algo Order API endpoints instead."}, status=400)   # como el real (4-nov)
        if path == "/fapi/v1/order/test" and m == "POST":
            self.tests.append(q)
            return web.json_response({})
        if path == "/fapi/v1/algoOrder" and m == "POST":
            a = {"algoId": len(self.algos) + 1, "clientAlgoId": q["clientAlgoId"], "algoType": q["algoType"],
                 "orderType": q["type"], "symbol": q["symbol"], "side": q["side"], "triggerPrice": q["triggerPrice"],
                 "workingType": q.get("workingType", "CONTRACT_PRICE"), "closePosition": q.get("closePosition") == "true",
                 "quantity": q.get("quantity", "0"), "algoStatus": "NEW", "created": time.monotonic()}
            self.algos[q["clientAlgoId"]] = a
            return web.json_response(a)
        if path == "/fapi/v1/algoOrder" and m == "GET":
            a = self.algos.get(q.get("clientAlgoId"))
            if a and a.get("lag"):
                a["lag"] -= 1
                return web.json_response({**a, "algoStatus": "NEW"})      # todavía no muestra la cancelación
            return web.json_response(a) if a else web.json_response({"code": -2013, "msg": "Order does not exist."},
                                                                    status=400)
        if path == "/fapi/v1/algoOrder" and m == "DELETE":
            a = self.algos[q["clientAlgoId"]]
            if not self.algo_stuck:
                a["algoStatus"], a["lag"] = "CANCELED", self.algo_lag
            self.cancel_after.append(time.monotonic() - a["created"])
            return web.json_response({"algoId": a["algoId"], "clientAlgoId": a["clientAlgoId"], "code": "200",
                                      "msg": "success"})
        if path == "/fapi/v1/openAlgoOrders":
            return web.json_response([a for a in self.algos.values() if a["algoStatus"] == "NEW"])
        if path == "/fapi/v1/algoOpenOrders" and m == "DELETE":
            for a in self.algos.values():
                a["algoStatus"] = "CANCELED"
            return web.json_response({"code": 200, "msg": "success"})
        if path == "/fapi/v1/order" and m == "POST":
            if q.get("timeInForce") == "GTX" and q["side"] == "BUY" and D(q["price"]) >= self.ask:
                return web.json_response({"code": -5022, "msg": "Due to the order could not be executed as maker"},
                                         status=400)
            side = ("SELL" if q["side"] == "BUY" else "BUY") if self.swap_sides else q["side"]
            o = {"symbol": q["symbol"], "clientOrderId": q["newClientOrderId"], "orderId": len(self.orders) + 1,
                 "side": side, "type": q["type"], "timeInForce": q.get("timeInForce"), "price": q.get("price", "0"),
                 "origQty": q.get("quantity", "0"), "executedQty": "0", "status": "NEW", "created": time.monotonic()}
            self.orders[q["newClientOrderId"]] = o
            return web.json_response(o)
        if path == "/fapi/v1/order" and m == "GET":
            o = self.orders.get(q.get("origClientOrderId"))
            return web.json_response(o) if o else web.json_response({"code": -2013, "msg": "Order does not exist."},
                                                                    status=400)
        if path == "/fapi/v1/order" and m == "DELETE":
            o = self.orders[q["origClientOrderId"]]
            o["status"] = "CANCELED"
            self.cancel_after.append(time.monotonic() - o["created"])
            return web.json_response(o)
        if path == "/fapi/v1/allOpenOrders" and m == "DELETE":
            if self.fail_cancel_all:
                return web.json_response({"code": -1000, "msg": "An unknown error occured"}, status=400)
            for o in self.orders.values():
                o["status"] = "CANCELED"
            return web.json_response({"code": 200, "msg": "The operation of cancel all open order is done."})
        return web.json_response({"code": -1, "msg": f"ruta desconocida {m} {path}"}, status=404)

    # -- datos públicos de mercado ------------------------------------------------------------------------
    def _now(self) -> float:
        return self.clock.time() if self.clock is not None else time.time()

    def _spec(self, sym: str) -> dict:
        tick, step, min_qty, notional, ctype, under, base = SPECS[sym]
        return {"symbol": sym, "pair": sym, "contractType": ctype, "status": "TRADING", "baseAsset": base,
                "quoteAsset": "USDT", "marginAsset": "USDT", "maintMarginPercent": "2.5000",
                "requiredMarginPercent": "5.0000", "underlyingType": under,
                "underlyingSubType": ["TradFi"] if ctype == "TRADIFI_PERPETUAL" else ["Crypto"],
                "filters": [{"filterType": "PRICE_FILTER", "tickSize": tick, "minPrice": tick, "maxPrice": "1000000"},
                            {"filterType": "LOT_SIZE", "stepSize": step, "minQty": min_qty, "maxQty": "1000000"},
                            {"filterType": "MIN_NOTIONAL", "notional": notional},
                            {"filterType": "PERCENT_PRICE", "multiplierUp": "1.05", "multiplierDown": "0.95"}]}

    def _mid(self, sym: str, m: int) -> D:
        px = self.price(sym, m)
        if px is None and sym == "XRPUSDT":
            return (self.bid + self.ask) / 2
        return D(str(px))

    def _public(self, path: str, q: dict):
        now_m = int(self._now() * 1000) // MIN * MIN
        if path == "/fapi/v1/depth":
            sym = q["symbol"]
            if sym == "XRPUSDT" and self.price(sym, now_m) is None:
                bid, ask, tick = self.bid, self.ask, D("0.0001")
            else:
                mid, tick = self._mid(sym, now_m), D(SPECS[sym][0])
                half = mid * D(str(self.spread_bps)) / 2 / 10_000
                bid = ((mid - half) / tick).to_integral_value(ROUND_FLOOR) * tick
                ask = max(((mid + half) / tick).to_integral_value(ROUND_CEILING) * tick, bid + tick)
            ms = int(self._now() * 1000)
            return web.json_response({"lastUpdateId": 1, "E": ms, "T": ms,
                                      "bids": [[str(bid - i * tick), str(self.level)] for i in range(20)],
                                      "asks": [[str(ask + i * tick), str(self.level)] for i in range(20)]})
        if path == "/fapi/v1/klines":
            sym, start, end = q["symbol"], int(q["startTime"]), int(q.get("endTime") or now_m)
            out, m = [], start // MIN * MIN
            while m <= min(end, now_m) and len(out) < int(q.get("limit", 500)):
                o, c = float(self._mid(sym, m)), float(self._mid(sym, m + MIN))
                h, lo = max(o, c) * 1.001, min(o, c) * 0.999
                if (sym, m) in self.overrides:
                    o, h, lo, c = self.overrides[(sym, m)]
                out.append([m, str(o), str(h), str(lo), str(c), "100", m + MIN - 1, str(100 * o), 10, "50",
                            str(50 * o), "0"])
                m += MIN
            return web.json_response(out)
        if path == "/fapi/v1/ticker/24hr":
            rows = [{"symbol": s_, "lastPrice": str(self._mid(s_, now_m)), "quoteVolume": str(VOLUMES.get(s_, 2e9))}
                    for s_ in SPECS]
            if "symbol" in q:
                return web.json_response(next(r for r in rows if r["symbol"] == q["symbol"]))
            return web.json_response(rows)
        if path == "/fapi/v1/premiumIndex":
            sym = q["symbol"]
            hours = 4 if sym == "XAUUSDT" else 8
            nxt = (int(self._now() * 1000) // (hours * 3600_000) + 1) * hours * 3600_000
            return web.json_response({"symbol": sym, "markPrice": str(self._mid(sym, now_m)),
                                      "lastFundingRate": str(self.funding_rate), "nextFundingTime": nxt})
        return web.json_response([{"symbol": "XAUUSDT", "fundingIntervalHours": 4}])      # /fapi/v1/fundingInfo

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_route("*", "/{tail:.*}", self.handle)
        return a


def signed_query(params: dict) -> str:
    q = urlencode(params)
    return q + "&signature=" + hmac.new(SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
