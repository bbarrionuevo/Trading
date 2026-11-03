"""SIMULACRO del modo real: el mismo contrato que `TradingClient`, pero NUNCA manda órdenes. Ruta protegida (G-2).

Sirve para probar el dinero real sin plata (pedido del dueño, 3-nov): el operador real completo (misma noticia,
misma decisión, mismo venue, el mismo cuerpo de cada orden que se mandaría) corre en la PC, y cada orden se
ejecuta contra el libro REAL de MEXC del momento (profundidad pública), con la comisión de taker de la API.
Lleva órdenes, ejecuciones, posiciones y saldo, y también simula el stop de emergencia del exchange.
`sent` guarda los cuerpos exactos que habría recibido MEXC.

Con `state_path`, el "exchange simulado" se guarda en disco después de cada cambio: un reinicio de la PC no
le borra las posiciones (si no, la conciliación del venue real vería diferencias y frenaría las entradas).
"""
import asyncio
from decimal import Decimal
from pathlib import Path

from ... import paths
from ...marketdata import mexc
from .mexc_futures import (
    CANCELED,
    CLOSE_LONG,
    CLOSE_SHORT,
    FILLED,
    IOC,
    LIMIT,
    OPEN,
    OPEN_LONG,
    OPEN_SHORT,
    POST_ONLY,
    TradingError,
)

TAKER = Decimal("0.0005")          # comisión de taker de la API de futuros de MEXC (anuncio del 31-mar-2026)
KEEP = 200                         # órdenes y ejecuciones que se guardan (las últimas)


class DryRunClient:
    simulated = True

    def __init__(self, books, session, *, capital: float, clock, base: str = mexc.BASE, taker: Decimal = TAKER,
                 state_path: Path | None = None):
        self.books, self.session, self.clock, self.taker, self.mexc_base = books, session, clock, taker, base
        self.state_path = Path(state_path) if state_path else None
        self._sizes: dict[str, Decimal] = {}
        self.balance = Decimal(str(capital))
        self.orders: dict[str, dict] = {}
        self.fills: dict[str, list] = {}
        self.pos: dict[tuple[str, int], dict] = {}
        self.sent: list[dict] = []
        self._next = 0
        self.base = "simulacro"
        self._load()

    # -- estado en disco ----------------------------------------------------------------------------------
    def _load(self) -> None:
        st = paths.read_json(self.state_path, None) if self.state_path else None
        if not isinstance(st, dict):
            return
        self.balance = Decimal(str(st.get("balance", self.balance)))
        self._next = int(st.get("next", 0))
        self.orders = {str(o["orderId"]): o for o in st.get("orders", [])}
        self.fills = {str(k): v for k, v in (st.get("deals") or {}).items()}
        self.pos = {(p["symbol"], int(p["positionType"])): p for p in st.get("positions", [])}
        self.sent = list(st.get("sent", []))

    async def _save(self) -> None:
        if self.state_path is None:
            return
        orders = list(self.orders.values())[-KEEP:]
        self.orders = {o["orderId"]: o for o in orders}
        self.fills = {k: v for k, v in self.fills.items() if k in self.orders}
        self.sent = self.sent[-50:]
        data = {"balance": str(self.balance), "next": self._next, "orders": orders, "deals": self.fills,
                "positions": list(self.pos.values()), "sent": self.sent}
        await asyncio.to_thread(paths.write_json_atomic, self.state_path, data)

    # -- lectura ------------------------------------------------------------------------------------------
    async def _size(self, symbol: str) -> Decimal:
        if symbol not in self._sizes:
            self._sizes[symbol] = Decimal((await mexc.detail(self.session, symbol, base=self.mexc_base))["contract_size"])
        return self._sizes[symbol]

    async def usdt(self) -> dict:
        """Como MEXC: el disponible es el saldo menos el margen de lo abierto (margen aislado)."""
        used = Decimal(0)
        for (sym, _), p in self.pos.items():
            used += Decimal(p["holdVol"]) * await self._size(sym) * Decimal(str(p["holdAvgPrice"])) / int(p["leverage"])
        return {"equity": float(self.balance), "available": float(self.balance - used)}

    async def positions(self, symbol: str | None = None) -> list[dict]:
        await self._emergency_stops()
        return [dict(p) for (s, _), p in self.pos.items() if p["holdVol"] > 0 and (symbol is None or s == symbol)]

    async def position_mode(self) -> int:
        return 1

    async def order(self, order_id: str) -> dict:
        return dict(self.orders.get(str(order_id), {}))

    async def deals(self, order_id: str) -> list[dict]:
        return list(self.fills.get(str(order_id), []))

    async def open_orders(self, symbol: str) -> list[dict]:
        return [o for o in self.orders.values() if o["symbol"] == symbol and o["state"] == OPEN]

    # -- órdenes (simuladas) --------------------------------------------------------------------------------
    async def submit(self, *, symbol: str, side: int, vol: int, price, leverage: int, order_type: int,
                     external_oid: str, stop_loss=None, position_id=None) -> str:
        if side not in (OPEN_LONG, CLOSE_SHORT, OPEN_SHORT, CLOSE_LONG) or order_type not in (LIMIT, POST_ONLY, IOC):
            raise ValueError(f"orden no permitida: lado {side}, tipo {order_type}")
        if int(vol) < 1 or int(leverage) < 1:
            raise ValueError("volumen y apalancamiento tienen que ser ≥ 1")
        body = {"symbol": symbol, "price": float(str(price)), "vol": int(vol), "leverage": int(leverage), "side": side,
                "type": order_type, "openType": 1, "externalOid": external_oid}
        if stop_loss is not None:
            body["stopLossPrice"] = float(str(stop_loss))
        if position_id is not None:
            body["positionId"] = position_id
        self.sent.append(body)
        self._next += 1
        oid = f"S{self._next}"
        o = {"orderId": oid, "symbol": symbol, "price": body["price"], "vol": int(vol), "leverage": int(leverage),
             "side": side, "orderType": order_type, "openType": 1, "state": OPEN, "dealVol": 0, "dealAvgPrice": 0.0,
             "externalOid": external_oid, "stopLossPrice": body.get("stopLossPrice")}
        size = await self._size(symbol)
        book = await self.books.book(symbol)
        buying = side in (OPEN_LONG, CLOSE_SHORT)
        best = book.best_ask if buying else book.best_bid
        cap = Decimal(str(price))
        crosses = best is not None and (best <= cap if buying else best >= cap)
        if order_type == POST_ONLY and crosses:
            raise TradingError("simulacro: la orden solo maker cruzaría el libro")
        self.orders[oid] = o
        if crosses:
            self._fill(o, Decimal(best), size, stop_loss)
        elif order_type == IOC:
            o["state"] = CANCELED
        await self._save()
        return oid

    def _fill(self, o: dict, price: Decimal, size: Decimal, stop_loss=None) -> None:
        sym, side = o["symbol"], o["side"]
        ptype = 1 if side in (OPEN_LONG, CLOSE_LONG) else 2
        opening = side in (OPEN_LONG, OPEN_SHORT)
        pos = self.pos.get((sym, ptype))
        if pos is None and not opening:                      # cerrar lo que no hay: el exchange no ejecuta nada
            o["state"] = CANCELED
            return
        if pos is None:
            pos = self.pos[(sym, ptype)] = {"positionId": self._next, "symbol": sym, "positionType": ptype,
                                            "holdVol": 0, "holdAvgPrice": 0.0, "leverage": o["leverage"],
                                            "openType": 1}
        vol = o["vol"] if opening else min(o["vol"], pos["holdVol"])
        if vol <= 0:
            o["state"] = CANCELED
            return
        fee = Decimal(vol) * size * price * self.taker
        profit = Decimal(0)
        if opening:
            old = Decimal(pos["holdVol"])
            pos["holdAvgPrice"] = float((Decimal(str(pos["holdAvgPrice"])) * old + price * vol) / (old + vol))
            pos["holdVol"] += vol
            if stop_loss is not None:
                pos["stopLossPrice"] = float(str(stop_loss))
        else:
            sign = 1 if ptype == 1 else -1
            profit = (price - Decimal(str(pos["holdAvgPrice"]))) * Decimal(vol) * size * sign
            pos["holdVol"] -= vol
            if pos["holdVol"] <= 0:                          # posición cerrada: el exchange la da de baja
                self.pos.pop((sym, ptype), None)
        self.balance += profit - fee
        o.update(dealVol=vol, dealAvgPrice=float(price), state=FILLED)
        self.fills[o["orderId"]] = [{"orderId": o["orderId"], "symbol": sym, "side": side, "vol": vol,
                                     "price": float(price), "fee": float(fee), "profit": float(profit),
                                     "isTaker": True, "timestamp": int(self.clock.time() * 1000)}]

    async def _emergency_stops(self) -> None:
        """Lo que haría el exchange solo: si el precio cruzó el stop de emergencia, cierra a ese precio."""
        fired = False
        for (sym, ptype), p in list(self.pos.items()):
            stop = p.get("stopLossPrice")
            if not p["holdVol"] or stop is None:
                continue
            book = await self.books.book(sym)
            px = book.best_bid if ptype == 1 else book.best_ask
            if px is None or not ((px <= Decimal(str(stop))) if ptype == 1 else (px >= Decimal(str(stop)))):
                continue
            self._next += 1
            o = {"orderId": f"S{self._next}", "symbol": sym, "price": float(stop), "vol": p["holdVol"],
                 "leverage": p["leverage"], "side": CLOSE_LONG if ptype == 1 else CLOSE_SHORT, "orderType": IOC,
                 "openType": 1, "state": OPEN, "dealVol": 0, "dealAvgPrice": 0.0, "externalOid": "emergencia"}
            self.orders[o["orderId"]] = o
            self._fill(o, Decimal(px), await self._size(sym))
            fired = True
        if fired:
            await self._save()

    async def cancel(self, order_ids: list[str]) -> list:
        for oid in order_ids:
            if str(oid) in self.orders and self.orders[str(oid)]["state"] == OPEN:
                self.orders[str(oid)]["state"] = CANCELED
        await self._save()
        return [{"orderId": oid, "errorCode": 0} for oid in order_ids]

    async def cancel_all(self, symbol: str) -> None:
        for o in self.orders.values():
            if o["symbol"] == symbol and o["state"] == OPEN:
                o["state"] = CANCELED
        await self._save()

    async def set_leverage(self, symbol: str, leverage: int, long: bool) -> None:
        return None
