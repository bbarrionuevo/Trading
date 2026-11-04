"""SIMULACRO LOCAL del dinero real en Binance (pedido del dueño, 4-nov-2026: "Dry-Run local usando la API de
producción"): el operador real completo sobre los datos de PRODUCCIÓN de Binance Futures USDⓈ-M, pero NINGUNA orden
sale de la PC. Ruta protegida (G-2).

- El mismo contrato que `BinanceClient` (lo que usa `BinanceVenue`): el venue, el motor y el riesgo son exactamente
  los del dinero real; solo cambia quién ejecuta.
- Cada orden se ejecuta contra el libro REAL del momento (`/fapi/v1/depth` de producción, público, pedido otra vez al
  llegar la orden: así cuenta la demora de ida a Binance), nivel por nivel y con su tope de precio: lo que no entra en
  el tope no se ejecuta (IOC). Se toma como mucho la mitad de lo visible en cada nivel (otros llegan antes).
- Comisión: la de taker de la cuenta real para ese contrato (en la PC, el 4-nov: 0,05 % cripto, 0,04 % TradFi), leída
  con la clave de SOLO LECTURA; sin ella, 0,05 %.
- El stop de emergencia se simula como lo haría Binance: si el precio de marca (`premiumIndex`, público) cruza el
  disparo, cierra la posición a mercado contra el libro. Al cerrarse una posición, su stop vence solo (EXPIRED).
- **Aislamiento del capital real:** este módulo no tiene ninguna ruta de órdenes (lo fija `test_a3`) y nunca recibe la
  clave de trading. La única clave que puede usar es la de SOLO LECTURA (`BINANCE_LECTURA_KEY`), y solo para leer el
  saldo y la comisión de la cuenta: Binance la rechaza para operar (código -2015, probado en la PC el 4-nov).
- Cada orden simulada queda en `ordenes-simuladas.jsonl` (hora, contrato, lado, cantidad, tope, precio, comisión,
  resultado y demora del libro). El exchange simulado se guarda en disco: un reinicio no lo borra.
"""
import asyncio
import json
import logging
import time
from decimal import Decimal
from pathlib import Path

from ... import paths
from .binance_futures import BUY, GTX, IOC, SELL, BinanceClient, BinanceError

log = logging.getLogger("tradingagent.simulacro")
TAKER = Decimal("0.0005")             # comisión de taker por defecto (USDⓈ-M, VIP 0)
PARTICIPATION = Decimal("0.5")        # parte de lo visible en cada nivel que se puede tomar
MARK_TTL_S = 1.0                      # el precio de marca se relee como mucho una vez por segundo por contrato
FEE_TTL_S = 6 * 3600
KEEP = 300                            # órdenes y ejecuciones que se guardan (las últimas)


class LocalDryRunClient:
    simulated = True
    base = "simulacro-local"

    def __init__(self, books, session, *, capital: float, clock, public_base: str | None = None,
                 reader: BinanceClient | None = None, state_path: Path | None = None, log_path: Path | None = None,
                 participation: Decimal = PARTICIPATION):
        from ...marketdata import binance_futures as bfd
        self.books, self.session, self.clock, self.reader = books, session, clock, reader
        self.public_base = public_base or bfd.BASE
        self.participation = participation
        self.state_path = Path(state_path) if state_path else None
        self.log_path = Path(log_path) if log_path else (self.state_path.with_name("ordenes-simuladas.jsonl")
                                                         if self.state_path else None)
        self.balance = Decimal(str(capital))
        self.positions_: dict[str, dict] = {}        # contrato → {"amt", "entry", "leverage"}
        self.orders: dict[str, dict] = {}            # id propio → orden
        self.trades: dict[str, list] = {}            # orderId → ejecuciones
        self.algos: dict[str, dict] = {}             # id propio → stop de emergencia
        self.leverage: dict[str, int] = {}
        self._fees: dict[str, tuple[float, Decimal]] = {}
        self._marks: dict[str, tuple[float, Decimal]] = {}
        self._next = 0
        self._load()

    # -- estado en disco ----------------------------------------------------------------------------------
    def _load(self) -> None:
        st = paths.read_json(self.state_path, None) if self.state_path else None
        if not isinstance(st, dict):
            return
        self.balance = Decimal(str(st.get("balance", self.balance)))
        self._next = int(st.get("next", 0))
        self.positions_ = {k: {"amt": Decimal(v["amt"]), "entry": Decimal(v["entry"]), "leverage": int(v["leverage"])}
                           for k, v in (st.get("positions") or {}).items()}
        self.orders = {o["clientOrderId"]: o for o in st.get("orders", [])}
        self.trades = {str(k): v for k, v in (st.get("trades") or {}).items()}
        self.algos = {a["clientAlgoId"]: a for a in st.get("algos", [])}
        self.leverage = {k: int(v) for k, v in (st.get("leverage") or {}).items()}

    async def _save(self) -> None:
        if self.state_path is None:
            return
        orders = list(self.orders.values())[-KEEP:]
        self.orders = {o["clientOrderId"]: o for o in orders}
        keep = {str(o["orderId"]) for o in orders}
        self.trades = {k: v for k, v in self.trades.items() if k in keep}
        algos = [a for a in self.algos.values() if a["algoStatus"] == "NEW"] + \
            [a for a in self.algos.values() if a["algoStatus"] != "NEW"][-50:]
        self.algos = {a["clientAlgoId"]: a for a in algos}
        data = {"balance": str(self.balance), "next": self._next, "orders": orders, "trades": self.trades,
                "positions": {k: {"amt": str(v["amt"]), "entry": str(v["entry"]), "leverage": v["leverage"]}
                              for k, v in self.positions_.items()},
                "algos": algos, "leverage": self.leverage}
        await asyncio.to_thread(paths.write_json_atomic, self.state_path, data)

    async def _log(self, row: dict) -> None:
        if self.log_path is None:
            return

        def write():
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        await asyncio.to_thread(write)

    # -- datos reales (lectura) -------------------------------------------------------------------------------
    async def _fee(self, symbol: str) -> Decimal:
        """Comisión de taker de la cuenta real (clave de solo lectura), con caché; sin clave, la estándar."""
        now = time.time()
        cached = self._fees.get(symbol)
        if cached and now - cached[0] < FEE_TTL_S:
            return cached[1]
        fee = TAKER
        if self.reader is not None:
            try:
                d = await self.reader.request("GET", "/fapi/v1/commissionRate", {"symbol": symbol})
                fee = Decimal(str(d.get("takerCommissionRate", TAKER)))
            except (BinanceError, PermissionError, ValueError) as e:
                log.warning("simulacro: sin la comisión de la cuenta para %s (%s): uso %s", symbol, e, TAKER)
        self._fees[symbol] = (now, fee)
        return fee

    async def _mark(self, symbol: str) -> Decimal | None:
        from ...marketdata import binance_futures as bfd
        cached = self._marks.get(symbol)
        if cached and self.clock.time() - cached[0] < MARK_TTL_S:
            return cached[1]
        try:
            p = await bfd.premium(self.session, symbol, self.public_base)
        except Exception as e:                                       # noqa: BLE001 (sin marca, el medio del libro)
            log.warning("simulacro: sin precio de marca de %s: %s", symbol, e)
            book = await self.books.book(symbol)
            return book.mid
        self._marks[symbol] = (self.clock.time(), Decimal(str(p["mark"])))
        return self._marks[symbol][1]

    async def real_balance(self) -> float | None:
        """El saldo REAL de la cuenta de futuros (clave de solo lectura), para empezar con el mismo capital."""
        if self.reader is None:
            return None
        try:
            return float((await self.reader.usdt())["equity"])
        except (BinanceError, PermissionError, ValueError, KeyError) as e:
            log.warning("simulacro: no pude leer el saldo real: %s", e)
            return None

    # -- el contrato de BinanceClient -----------------------------------------------------------------------
    async def sync_time(self) -> int:
        return 0

    async def usdt(self) -> dict:
        await self._check_stops()
        used = sum((abs(p["amt"]) * p["entry"] / max(1, p["leverage"]) for p in self.positions_.values()), Decimal(0))
        return {"equity": float(self.balance), "available": float(self.balance - used)}

    async def positions(self, symbol: str | None = None) -> list[dict]:
        await self._check_stops()
        return [{"symbol": s, "positionAmt": str(p["amt"]), "entryPrice": str(p["entry"]), "marginType": "isolated",
                 "leverage": str(p["leverage"])} for s, p in self.positions_.items()
                if p["amt"] and (symbol is None or s == symbol)]

    async def set_isolated(self, symbol: str) -> None:
        return None

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self.leverage[symbol] = int(leverage)

    async def open_orders(self, symbol: str) -> list[dict]:
        return []                                             # IOC: nada queda en el libro

    async def order(self, symbol: str, client_id: str) -> dict:
        o = self.orders.get(client_id)
        if o is None or o["symbol"] != symbol:
            raise BinanceError("simulacro: la orden no existe", code=-2013)
        return dict(o)

    async def fills(self, symbol: str, order_id) -> list[dict]:
        return list(self.trades.get(str(order_id), []))

    async def cancel(self, symbol: str, client_id: str) -> dict:
        return dict(self.orders.get(client_id) or {})

    async def cancel_all(self, symbol: str) -> dict:
        return {"code": 200, "msg": "simulacro"}

    async def submit(self, *, symbol: str, side: str, qty, price, tif: str, client_id: str,
                     reduce_only: bool = False) -> dict:
        if side not in (BUY, SELL) or tif not in (IOC, GTX) or price is None or not Decimal(str(qty)) > 0:
            raise ValueError("solo órdenes límite IOC o solo maker, con precio y cantidad")
        if tif == GTX:
            raise ValueError("simulacro: solo órdenes IOC")
        if client_id in self.orders:                          # una intención = una orden (D-1)
            return dict(self.orders[client_id])
        qty, cap = Decimal(str(qty)), Decimal(str(price))
        held = self.positions_.get(symbol, {}).get("amt", Decimal(0))
        if reduce_only:
            if held == 0 or (held > 0) == (side == BUY):
                raise BinanceError("simulacro: ReduceOnly Order is rejected.", code=-2022)
            qty = min(qty, abs(held))
        t0 = time.perf_counter()
        book = await self.books.book(symbol)                  # el libro real cuando llegaría la orden
        book_ms = round(1000 * (time.perf_counter() - t0))
        walk = book.walk("buy" if side == BUY else "sell", qty=qty, limit_price=cap, participation=self.participation)
        self._next += 1
        oid = self._next
        o = {"orderId": oid, "clientOrderId": client_id, "symbol": symbol, "side": side, "type": "LIMIT",
             "timeInForce": tif, "price": str(cap), "origQty": str(qty), "executedQty": "0", "avgPrice": "0",
             "status": "EXPIRED", "reduceOnly": reduce_only, "updateTime": int(self.clock.time() * 1000)}
        pnl = fee = Decimal(0)
        if walk.filled_qty > 0:
            avg = walk.avg_price
            fee = walk.filled_qty * avg * await self._fee(symbol)
            pnl = self._apply(symbol, side, walk.filled_qty, avg, fee)
            o.update(executedQty=str(walk.filled_qty), avgPrice=str(avg),
                     status="FILLED" if walk.filled_qty >= qty else "EXPIRED")
            self.trades[str(oid)] = [{"orderId": oid, "symbol": symbol, "side": side, "price": str(avg),
                                      "qty": str(walk.filled_qty), "commission": str(fee), "commissionAsset": "USDT",
                                      "realizedPnl": str(pnl), "maker": False, "time": o["updateTime"]}]
        self.orders[client_id] = o
        await self._log({"ts": round(self.clock.time(), 3), "tipo": "orden", "id": client_id, "contrato": symbol,
                         "lado": side, "cantidad": str(qty), "tope": str(cap), "ejecutado": o["executedQty"],
                         "precio": o["avgPrice"], "comision": str(fee), "resultado": str(pnl), "solo_reducir":
                         reduce_only, "mejor": str(book.best_ask if side == BUY else book.best_bid),
                         "libro_ms": book_ms, "saldo": str(self.balance)})
        await self._save()
        return dict(o)

    def _apply(self, symbol: str, side: str, qty: Decimal, price: Decimal, fee: Decimal) -> Decimal:
        """Aplica una ejecución a la posición y al saldo; devuelve el resultado realizado (sin la comisión)."""
        pos = self.positions_.setdefault(symbol, {"amt": Decimal(0), "entry": Decimal(0),
                                                  "leverage": self.leverage.get(symbol, 1)})
        signed = qty if side == BUY else -qty
        realized = Decimal(0)
        if pos["amt"] == 0 or (pos["amt"] > 0) == (signed > 0):
            new = pos["amt"] + signed
            pos["entry"] = (abs(pos["amt"]) * pos["entry"] + qty * price) / abs(new)
            pos["amt"] = new
        else:
            closing = min(qty, abs(pos["amt"]))
            realized = (price - pos["entry"]) * closing * (1 if pos["amt"] > 0 else -1)
            pos["amt"] += signed
        if pos["amt"] == 0:
            del self.positions_[symbol]
            for a in self.algos.values():                     # como Binance: el stop de cierre total vence solo
                if a["symbol"] == symbol and a["algoStatus"] == "NEW" and a.get("closePosition"):
                    a["algoStatus"] = "EXPIRED"
        self.balance += realized - fee
        return realized

    # -- stop de emergencia simulado ------------------------------------------------------------------------
    async def emergency_stop(self, *, symbol: str, side: str, trigger_price, client_id: str, qty=None) -> dict:
        if side not in (BUY, SELL) or trigger_price is None:
            raise ValueError("el stop necesita lado y precio de disparo")
        a = {"clientAlgoId": client_id[:36], "algoId": len(self.algos) + 1, "algoType": "CONDITIONAL",
             "orderType": "STOP_MARKET", "symbol": symbol, "side": side, "triggerPrice": str(trigger_price),
             "workingType": "MARK_PRICE", "closePosition": qty is None, "quantity": str(qty or 0),
             "algoStatus": "NEW", "createTime": int(self.clock.time() * 1000)}
        self.algos[a["clientAlgoId"]] = a
        await self._save()
        return dict(a)

    async def stop(self, client_id: str) -> dict:
        a = self.algos.get(client_id)
        if a is None:
            raise BinanceError("simulacro: el stop no existe", code=-2013)
        return dict(a)

    async def open_stops(self, symbol: str) -> list[dict]:
        return [dict(a) for a in self.algos.values() if a["symbol"] == symbol and a["algoStatus"] == "NEW"]

    async def cancel_stop(self, client_id: str) -> dict:
        a = self.algos.get(client_id)
        if a is None or a["algoStatus"] != "NEW":
            raise BinanceError("simulacro: Unknown order sent.", code=-2011)
        a["algoStatus"] = "CANCELED"
        await self._save()
        return {"clientAlgoId": client_id, "code": "200", "msg": "success"}

    async def cancel_all_stops(self, symbol: str) -> dict:
        for a in self.algos.values():
            if a["symbol"] == symbol and a["algoStatus"] == "NEW":
                a["algoStatus"] = "CANCELED"
        await self._save()
        return {"code": 200, "msg": "success"}

    async def _check_stops(self) -> None:
        """Lo que haría Binance solo: si el precio de marca cruzó un stop de cierre total, cierra a mercado."""
        fired = False
        for a in list(self.algos.values()):
            sym = a["symbol"]
            if a["algoStatus"] != "NEW" or not a.get("closePosition") or sym not in self.positions_:
                continue
            mark = await self._mark(sym)
            trigger = Decimal(a["triggerPrice"])
            if mark is None or not ((mark <= trigger) if a["side"] == SELL else (mark >= trigger)):
                continue
            amt = self.positions_[sym]["amt"]
            book = await self.books.book(sym)
            walk = book.walk("buy" if a["side"] == BUY else "sell", qty=abs(amt))
            if walk.filled_qty <= 0:
                continue
            avg = walk.avg_price
            fee = walk.filled_qty * avg * await self._fee(sym)
            pnl = self._apply(sym, a["side"], walk.filled_qty, avg, fee)
            a["algoStatus"] = "FINISHED"
            fired = True
            await self._log({"ts": round(self.clock.time(), 3), "tipo": "stop_de_emergencia", "contrato": sym,
                             "lado": a["side"], "disparo": a["triggerPrice"], "marca": str(mark),
                             "ejecutado": str(walk.filled_qty), "precio": str(avg), "comision": str(fee),
                             "resultado": str(pnl), "saldo": str(self.balance)})
        if fired:
            await self._save()
