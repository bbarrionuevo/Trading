"""MEXC falso CON órdenes (para el modo real): verifica la firma de GET (query ordenada) y POST (cuerpo JSON),
ejecuta IOC contra el libro del FakeMexc, rechaza la "solo maker" que cruzaría, lleva órdenes, ejecuciones y
posiciones, y registra el stop de emergencia. Fallas programables: lados cambiados, rechazo de órdenes."""
import hashlib
import hmac
import json
from decimal import Decimal as D

from aiohttp import web

from fake_mexc import ACCESS, SECRET, SPECS, FakeMexc, ok

TAKER = D("0.0006")


def fail(code, msg):
    return web.json_response({"success": False, "code": code, "message": msg})


class FakeMexcTrade(FakeMexc):
    def __init__(self, price, clock, **kw):
        super().__init__(price, clock, **kw)
        self.orders: dict[str, dict] = {}
        self.deals: dict[str, list] = {}
        self.positions: dict[tuple[str, int], dict] = {}     # (símbolo, 1 largo | 2 corto)
        self.balance = D("20")
        self.swap_sides = False                               # el exchange "entiende" otro lado
        self.reject_submit = ""                               # mensaje de rechazo para toda orden
        self.calls: list[tuple[str, str]] = []
        self._next = 1000

    # -- firma ----------------------------------------------------------------------------------------------
    async def _signed(self, request) -> tuple[bool, str]:
        body = await request.text() if request.method == "POST" else ""
        payload = body if request.method == "POST" else "&".join(f"{k}={v}" for k, v in sorted(request.query.items()))
        key, t, sig = request.headers.get("ApiKey"), request.headers.get("Request-Time"), request.headers.get("Signature")
        want = hmac.new(SECRET.encode(), f"{ACCESS}{t}{payload}".encode(), hashlib.sha256).hexdigest()
        good = key == ACCESS and sig == want
        self.signed.append({"path": request.path, "method": request.method, "ok": good})
        return good, body

    def _book(self, sym):
        unit = D(SPECS[sym][1])
        mid = D(str(self.price(sym, self.now_m())))
        half = mid * D(str(self.spread_bps)) / 2 / 10_000
        from decimal import ROUND_CEILING, ROUND_FLOOR
        bid = ((mid - half) / unit).to_integral_value(ROUND_FLOOR) * unit
        ask = max(((mid + half) / unit).to_integral_value(ROUND_CEILING) * unit, bid + unit)
        return bid, ask

    # -- órdenes ------------------------------------------------------------------------------------------------
    def _fill(self, o: dict, price: D) -> None:
        sym, vol, side = o["symbol"], int(o["vol"]), int(o["side"])
        size = D(SPECS[sym][0])
        ptype = 1 if side in (1, 4) else 2
        pos = self.positions.setdefault((sym, ptype), {"positionId": len(self.positions) + 1, "symbol": sym,
                                                       "positionType": ptype, "holdVol": 0, "holdAvgPrice": 0.0,
                                                       "leverage": o["leverage"], "openType": 1})
        opening = side in (1, 3)
        if not opening:
            vol = min(vol, int(pos["holdVol"]))
        if vol <= 0:
            o["state"] = 4
            return
        notional = D(vol) * size * price
        fee = notional * TAKER
        profit = D(0)
        if opening:
            old = D(int(pos["holdVol"]))
            pos["holdAvgPrice"] = float((D(str(pos["holdAvgPrice"])) * old + price * vol) / (old + vol))
            pos["holdVol"] = int(pos["holdVol"]) + vol
            if o.get("stopLossPrice") is not None:
                pos["stopLossPrice"] = o["stopLossPrice"]
        else:
            entry = D(str(pos["holdAvgPrice"]))
            profit = (price - entry) * D(vol) * size * (1 if ptype == 1 else -1)
            pos["holdVol"] = int(pos["holdVol"]) - vol
        self.balance += profit - fee
        o.update(dealVol=vol, dealAvgPrice=float(price), state=3)
        self.deals[o["orderId"]] = [{"orderId": o["orderId"], "symbol": sym, "side": side, "vol": vol,
                                     "price": float(price), "fee": float(fee), "profit": float(profit),
                                     "isTaker": True, "timestamp": int(self.clock.time() * 1000)}]

    async def submit(self, body: dict):
        if self.reject_submit:
            return fail(2001, self.reject_submit)
        sym, side, otype = body["symbol"], int(body["side"]), int(body["type"])
        if sym not in SPECS or side not in (1, 2, 3, 4) or otype not in (1, 2, 3, 4, 5) or int(body["vol"]) < 1:
            return fail(2002, "parámetros inválidos")
        recorded = {1: 3, 3: 1, 2: 4, 4: 2}[side] if self.swap_sides else side
        self._next += 1
        oid = str(self._next)
        price = D(str(body["price"]))
        o = {"orderId": oid, "symbol": sym, "price": float(price), "vol": int(body["vol"]), "leverage": body["leverage"],
             "side": recorded, "orderType": otype, "openType": body["openType"], "state": 2, "dealVol": 0,
             "dealAvgPrice": 0.0, "externalOid": body.get("externalOid"), "stopLossPrice": body.get("stopLossPrice")}
        bid, ask = self._book(sym)
        buying = recorded in (1, 2)
        crosses = price >= ask if buying else price <= bid
        if otype == 2 and crosses:
            return fail(2003, "post only: la orden cruzaría el libro")
        self.orders[oid] = o
        if crosses and otype in (1, 3, 4, 5):
            self._fill(o, ask if buying else bid)
        elif otype in (3, 4):
            o["state"] = 4                                    # IOC/FOK sin contraparte: cancelada
        return ok(oid)

    async def private(self, request):
        good, body = await self._signed(request)
        path, method = request.path, request.method
        self.calls.append((method, path))
        if not good:
            return fail(602, "Signature verification failed")
        data = json.loads(body) if body else None
        if path.endswith("/account/assets"):
            return ok([{"currency": "USDT", "equity": float(self.balance), "availableBalance": float(self.balance)}])
        if path.endswith("/account/tiered_fee_rate"):
            return ok({"level": 0, **self.account_fees})
        if path.endswith("/position/open_positions"):
            sym = request.query.get("symbol")
            return ok([p for (s, _), p in self.positions.items() if p["holdVol"] > 0 and (not sym or s == sym)])
        if path.endswith("/order/submit") and method == "POST":
            return await self.submit(data)
        if path.endswith("/order/cancel") and method == "POST":
            out = []
            for oid in data:
                o = self.orders.get(str(oid))
                if o and o["state"] in (1, 2):
                    o["state"] = 4
                out.append({"orderId": oid, "errorCode": 0 if o else 2009})
            return ok(out)
        if path.endswith("/order/cancel_all") and method == "POST":
            for o in self.orders.values():
                if o["symbol"] == data.get("symbol") and o["state"] in (1, 2):
                    o["state"] = 4
            return ok(None)
        if "/order/get/" in path:
            o = self.orders.get(path.rsplit("/", 1)[1])
            return ok(o) if o else fail(2009, "orden inexistente")
        if "/order/deal_details/" in path:
            return ok(self.deals.get(path.rsplit("/", 1)[1], []))
        if "/order/list/open_orders/" in path:
            sym = path.rsplit("/", 1)[1]
            return ok([o for o in self.orders.values() if o["symbol"] == sym and o["state"] in (1, 2)])
        if path.endswith("/position/change_leverage") and method == "POST":
            return ok(None)
        return fail(404, "no existe")

    def app(self) -> web.Application:
        a = super().app()
        return a
