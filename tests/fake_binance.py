"""Binance Futures USDⓈ-M falso para el modo real: verifica la firma HMAC de la query, valida órdenes (prueba sin
ejecutar), rechaza la "solo maker" (GTX) que cruzaría, lleva órdenes y posiciones, y programa fallas (límite 429,
modo multi-activo, cobertura, bloqueo por reglas cuantitativas, otro lado)."""
import hashlib
import hmac
import time
from decimal import Decimal as D
from urllib.parse import urlencode

from aiohttp import web

KEY, SECRET = "clave-binance-de-prueba", "secreto-binance-de-prueba"


class FakeBinance:
    def __init__(self, bid="0.5000", ask="0.5001"):
        self.bid, self.ask = D(bid), D(ask)
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
            return web.json_response({"symbols": [{"symbol": "XRPUSDT", "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.0001"},
                {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]})
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
            return web.json_response(a) if a else web.json_response({"code": -2013, "msg": "Order does not exist."},
                                                                    status=400)
        if path == "/fapi/v1/algoOrder" and m == "DELETE":
            a = self.algos[q["clientAlgoId"]]
            a["algoStatus"] = "CANCELED"
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
            for o in self.orders.values():
                o["status"] = "CANCELED"
            return web.json_response({"code": 200, "msg": "The operation of cancel all open order is done."})
        return web.json_response({"code": -1, "msg": f"ruta desconocida {m} {path}"}, status=404)

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_route("*", "/{tail:.*}", self.handle)
        return a


def signed_query(params: dict) -> str:
    q = urlencode(params)
    return q + "&signature=" + hmac.new(SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
