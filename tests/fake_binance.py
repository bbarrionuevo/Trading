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
        self.positions: dict[str, dict] = {}      # contrato → {"amt": con signo, "entry": precio promedio}
        self.trades: list[dict] = []              # ejecuciones (userTrades)
        self.fee = D("0.0005")                    # comisión de taker
        self.fail_next_order = 0                  # cuántas IOC se ejecutan pero contestan 503 (no se sabe si entró)
        self.fail_stops = 0                       # cuántos stops de emergencia rechaza
        self.tradfi_signed = True                 # el dueño aceptó el acuerdo TradFi (si no: -4411, como la demo)
        self.taker_rate = "0.0004"                # comisión de taker de la cuenta (commissionRate)

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
            sym = request.query.get("symbol", "XRPUSDT")
            bid, ask = self._top(sym)
            return web.json_response({"symbol": sym, "bidPrice": str(bid), "askPrice": str(ask)})
        if not self._check(request):
            return web.json_response({"code": -1022, "msg": "Signature for this request is not valid."}, status=400)
        q = dict(request.query)
        self._fire_stops()
        if path == "/fapi/v2/balance":
            used = sum((abs(x["amt"]) * x["entry"] / self.leverage.get(sym, 20) for sym, x in self.positions.items()),
                       D(0))
            return web.json_response([{"asset": "USDT", "balance": str(self.balance), "crossUnPnl": "0",
                                       "availableBalance": str(self.balance - used)}])
        if path == "/fapi/v1/apiTradingStatus":
            ind = {"XRPUSDT": [{"isLocked": True, "indicator": "UFR"}]} if self.locked else {}
            return web.json_response({"indicators": ind})
        if path == "/fapi/v1/positionSide/dual":
            return web.json_response({"dualSidePosition": self.dual})
        if path == "/fapi/v1/multiAssetsMargin":
            return web.json_response({"multiAssetsMargin": self.multi_assets})
        if path == "/fapi/v2/positionRisk":
            return web.json_response([{"symbol": sym, "positionAmt": str(x["amt"]), "entryPrice": str(x["entry"]),
                                       "marginType": "isolated", "leverage": str(self.leverage.get(sym, 20))}
                                      for sym, x in self.positions.items()
                                      if x["amt"] and q.get("symbol", sym) == sym])
        if path == "/fapi/v1/commissionRate":
            return web.json_response({"symbol": q["symbol"], "makerCommissionRate": "0.0002",
                                      "takerCommissionRate": self.taker_rate})
        if path == "/fapi/v1/userTrades":
            return web.json_response([t for t in self.trades if t["symbol"] == q["symbol"]
                                      and str(t["orderId"]) == str(q.get("orderId", t["orderId"]))])
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
            if self.fail_stops:
                self.fail_stops -= 1
                return web.json_response({"code": -1000, "msg": "Unknown error"}, status=400)
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
            a = self.algos.get(q["clientAlgoId"])
            if a is None or a["algoStatus"] != "NEW":                 # ya vencido, disparado o cancelado
                return web.json_response({"code": -2011, "msg": "Unknown order sent."}, status=400)
            if not self.algo_stuck:
                a["algoStatus"], a["lag"] = "CANCELED", self.algo_lag
            self.cancel_after.append(time.monotonic() - a["created"])
            return web.json_response({"algoId": a["algoId"], "clientAlgoId": a["clientAlgoId"], "code": "200",
                                      "msg": "success"})
        if path == "/fapi/v1/openAlgoOrders":
            return web.json_response([a for a in self.algos.values() if a["algoStatus"] == "NEW"
                                      and q.get("symbol", a["symbol"]) == a["symbol"]])
        if path == "/fapi/v1/algoOpenOrders" and m == "DELETE":
            for a in self.algos.values():
                if a["algoStatus"] == "NEW" and q.get("symbol", a["symbol"]) == a["symbol"]:
                    a["algoStatus"] = "CANCELED"
            return web.json_response({"code": 200, "msg": "success"})
        if path == "/fapi/v1/order" and m == "POST" and q.get("timeInForce") == "IOC":
            return self._ioc(q)
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

    # -- ejecución --------------------------------------------------------------------------------------
    def _top(self, sym: str) -> tuple[D, D]:
        now_m = int(self._now() * 1000) // MIN * MIN
        if sym == "XRPUSDT" and self.price(sym, now_m) is None:
            return self.bid, self.ask
        mid, tick = self._mid(sym, now_m), D(SPECS[sym][0])
        half = mid * D(str(self.spread_bps)) / 2 / 10_000
        bid = ((mid - half) / tick).to_integral_value(ROUND_FLOOR) * tick
        return bid, max(((mid + half) / tick).to_integral_value(ROUND_CEILING) * tick, bid + tick)

    def _fill(self, sym: str, side: str, qty: D, px: D, oid) -> None:
        pos = self.positions.setdefault(sym, {"amt": D(0), "entry": D(0)})
        signed = qty if side == "BUY" else -qty
        realized = D(0)
        if pos["amt"] == 0 or (pos["amt"] > 0) == (signed > 0):
            new = pos["amt"] + signed
            pos["entry"] = (abs(pos["amt"]) * pos["entry"] + qty * px) / abs(new)
            pos["amt"] = new
        else:
            realized = (px - pos["entry"]) * min(qty, abs(pos["amt"])) * (1 if pos["amt"] > 0 else -1)
            pos["amt"] += signed
        if pos["amt"] == 0:
            del self.positions[sym]
            for a in self.algos.values():                 # Binance vence solo el stop de cierre total (demo, 4-nov)
                if a["symbol"] == sym and a["algoStatus"] == "NEW" and a.get("closePosition"):
                    a["algoStatus"] = "EXPIRED"
        fee = qty * px * self.fee
        self.balance += realized - fee
        self.trades.append({"symbol": sym, "id": len(self.trades) + 1, "orderId": oid, "side": side, "price": str(px),
                            "qty": str(qty), "realizedPnl": str(realized), "quoteQty": str(qty * px),
                            "commission": str(fee), "commissionAsset": "USDT", "time": int(self._now() * 1000),
                            "maker": False, "buyer": side == "BUY"})

    def _ioc(self, q: dict):
        sym, side, qty, price = q["symbol"], q["side"], D(q["quantity"]), D(q["price"])
        reduce = q.get("reduceOnly") == "true"
        if SPECS[sym][4] == "TRADIFI_PERPETUAL" and not self.tradfi_signed:
            return web.json_response({"code": -4411, "msg": "Please sign TradFi-Perps agreement contract fapi."},
                                     status=400)
        pos = self.positions.get(sym, {"amt": D(0)})
        if reduce:
            if pos["amt"] == 0 or (pos["amt"] > 0) == (side == "BUY"):
                return web.json_response({"code": -2022, "msg": "ReduceOnly Order is rejected."}, status=400)
            qty = min(qty, abs(pos["amt"]))
        bid, ask = self._top(sym)
        px = ask if side == "BUY" else bid
        oid = len(self.orders) + 1
        o = {"symbol": sym, "clientOrderId": q["newClientOrderId"], "orderId": oid, "side": side, "type": "LIMIT",
             "timeInForce": "IOC", "price": q["price"], "origQty": q["quantity"], "executedQty": "0", "avgPrice": "0",
             "status": "EXPIRED", "reduceOnly": reduce, "created": time.monotonic()}
        if (px <= price) if side == "BUY" else (px >= price):
            self._fill(sym, side, qty, px, oid)
            o.update(executedQty=str(qty), avgPrice=str(px), status="FILLED")
        self.orders[q["newClientOrderId"]] = o
        if self.fail_next_order:                              # entró, pero la respuesta se perdió
            self.fail_next_order -= 1
            return web.json_response({"code": -1000, "msg": "Unknown error, please check your request or try again "
                                      "later."}, status=503)
        return web.json_response(o)

    def _fire_stops(self) -> None:
        """Lo que hace Binance solo: si el precio de marca cruzó un stop de cierre total, cierra la posición."""
        now_m = int(self._now() * 1000) // MIN * MIN
        for a in self.algos.values():
            if a["algoStatus"] != "NEW" or not a.get("closePosition") or a["symbol"] not in self.positions:
                continue
            mark, trigger = self._mid(a["symbol"], now_m), D(a["triggerPrice"])
            if (mark <= trigger) if a["side"] == "SELL" else (mark >= trigger):
                amt = self.positions[a["symbol"]]["amt"]
                self._fill(a["symbol"], a["side"], abs(amt), mark, f"algo{a['algoId']}")
                a["algoStatus"] = "FINISHED"

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
