"""Venue REAL de futuros de MEXC: la misma forma que el de práctica (`submit` / `cancel`), así el núcleo de
decisión y de riesgo es exactamente el mismo (C-3). Ruta protegida (G-2).

Antes de CADA orden:
- el armado local tiene que estar vigente (`arming.status`: archivo del dueño, misma clave, prueba sin riesgo
  aprobada, sin KILL);
- el margen de lo nuevo no puede pasar el saldo DISPONIBLE de la cuenta de futuros (sin tope de capital: se
  trabaja con todo el saldo; el tamaño lo decide el motor con los porcentajes del sobre);
- si la conciliación encontró diferencias con el exchange, no se abre nada nuevo (salir siempre se puede).

Cómo opera:
- Entradas y salidas son órdenes **IOC con tope de precio** (nunca a mercado sin tope: D-2), en margen aislado.
- Las salidas son de cierre (lado 2/4): nunca agrandan una posición.
- Cada entrada lleva un **stop de emergencia en el exchange** (`emergency_stop` en las etiquetas de la
  intención), por si la PC se apaga. El agente sale antes con su propio stop.
- Cada ejecución se registra en la misma cartera que usa el motor (`Portfolio.apply_fill`) con el precio y la
  comisión reales que informa MEXC.
"""
import asyncio
import logging
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ...marketdata import mexc
from ..orders import EXCHANGE_FLAT, Fill, Order, OrderEvent, OrderIntent, OrderStatus, Side
from . import arming
from .mexc_futures import DONE_STATES, FILLED, IOC, TradingClient, TradingError, side_code

log = logging.getLogger("tradingagent.real")
BPS = Decimal(10_000)
POLL_S, POLL_TRIES = 0.25, 24
BALANCE_TTL_S = 60                        # el saldo se relee en cada conciliación, después de cada ejecución y
MARGIN_BUFFER = Decimal("0.98")           # si tiene más de 1 min; se deja 2 % para comisiones


class RealVenue:
    def __init__(self, client: TradingClient, session, registry, books, portfolio, clock, *, home, access_key: str,
                 limits: dict, leverage: int = 1, base: str = mexc.BASE):
        self.client, self.session, self.registry, self.books = client, session, registry, books
        self.portfolio, self.clock, self.home, self.limits = portfolio, clock, home, limits
        self._key = access_key
        self.leverage = max(1, min(int(leverage), int(limits["per_trade"]["max_leverage"])))
        self.base = base
        self.orders: dict[str, Order] = {}
        self.marks: dict[str, Decimal] = {}
        self.on_event = None
        self.blocked = ""                         # motivo de la conciliación que impide entrar ("" = ok)
        self.balance: dict | None = None          # último saldo leído de la cuenta (equity, disponible)
        self._balance_at = 0.0
        self._specs: dict[str, dict] = {}
        self._seq = 0
        self.stats = {"ordenes": 0, "rechazadas": 0, "llenadas": 0}

    # -- utilidades -----------------------------------------------------------------------------------------
    def _emit(self, kind: str, order: Order, fill: Fill | None = None) -> None:
        if self.on_event is not None:
            self.on_event(OrderEvent(kind, {"client_id": order.intent.client_id, "symbol": order.intent.symbol,
                                            "status": str(order.status), "reason": order.reason,
                                            "role": order.intent.tags.get("role", "")}, fill, self.clock.time()))

    def _reject(self, order: Order, reason: str) -> Order:
        order.status, order.reason, order.updated_at = OrderStatus.REJECTED, reason, self.clock.time()
        self.stats["rechazadas"] += 1
        log.warning("orden real %s rechazada: %s", order.intent.client_id, reason)
        self._emit("rejected", order)
        return order

    async def _spec(self, symbol: str) -> dict:
        if symbol not in self._specs:
            d = await mexc.detail(self.session, symbol, base=self.base)
            self._specs[symbol] = {"size": Decimal(d["contract_size"]), "step": Decimal(d["step"]),
                                   "tick": Decimal(d["tick"]), "max_leverage": int(d["max_leverage"])}
        return self._specs[symbol]

    def open_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)]

    def summary(self) -> dict:
        return {**self.stats, "bloqueado": self.blocked, "apalancamiento": self.leverage}

    async def account(self, fresh: bool = False) -> dict:
        """Saldo de la cuenta de futuros: {'equity', 'available'} en USDT (con caché corta)."""
        if fresh or self.balance is None or self.clock.time() - self._balance_at > BALANCE_TTL_S:
            self.balance = await self.client.usdt()
            self._balance_at = self.clock.time()
        return self.balance

    # -- órdenes ------------------------------------------------------------------------------------------
    async def submit(self, intent: OrderIntent) -> Order:
        now = self.clock.time()
        self._seq += 1
        order = Order(order_id=f"R{self._seq}", intent=intent, status=OrderStatus.NEW, created_at=now, qty=Decimal(0),
                      ref_price=intent.ref_price)
        self.orders[intent.client_id] = order
        self.stats["ordenes"] += 1
        armed, why, arm = arming.status(self.home, self._key)
        if not intent.reduce_only:                            # salir siempre se puede (A-2): solo se frenan entradas
            if not armed:
                return self._reject(order, f"real_no_armado:{why}")
            if not arming.asset_allowed(arm, intent.symbol):
                return self._reject(order, "activo_no_armado")
            if self.blocked:
                return self._reject(order, f"conciliacion:{self.blocked}")
        inst = self.registry.get(intent.symbol)
        if inst is None:
            return self._reject(order, "instrumento_desconocido")
        try:
            spec = await self._spec(intent.symbol)
            book = await self.books.book(intent.symbol)
        except Exception as e:                                       # noqa: BLE001 (sin datos no se opera)
            return self._reject(order, f"sin_datos:{type(e).__name__}")
        buy = intent.side is Side.BUY
        ref = book.best_ask if buy else book.best_bid
        if ref is None:
            return self._reject(order, "libro_vacio")
        bps = min(Decimal(str(intent.max_slippage_bps or self.limits["per_trade"]["max_slippage_bps"])),
                  Decimal(str(self.limits["per_trade"]["max_slippage_bps"])))
        cap = ref * (1 + bps / BPS) if buy else ref * (1 - bps / BPS)
        cap = (cap / spec["tick"]).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR) * spec["tick"]
        qty = intent.qty if intent.qty is not None else (intent.notional or Decimal(0)) / ref
        held = self.portfolio.qty(intent.symbol)
        if intent.reduce_only:
            if held == 0 or (held > 0) == buy:
                return self._reject(order, "reduce_only_sin_posicion")
            qty = min(qty, abs(held))
        steps = (qty / spec["step"]).to_integral_value(rounding=ROUND_FLOOR)
        vol = int(steps * (spec["step"] / spec["size"]))
        if vol < 1:
            return self._reject(order, "menos_de_un_contrato")
        notional = Decimal(vol) * spec["size"] * ref
        if not intent.reduce_only:
            try:
                free = Decimal(str((await self.account())["available"]))
            except (TradingError, KeyError, TypeError, ValueError) as e:
                return self._reject(order, f"sin_saldo_leido:{str(e)[:80]}")
            if notional / Decimal(self.leverage) > free * MARGIN_BUFFER:
                return self._reject(order, "supera_el_saldo_disponible")
        opening = not intent.reduce_only
        long_side = buy if opening else held > 0
        order.qty, order.limit_price = Decimal(vol) * spec["size"], cap
        stop = intent.tags.get("emergency_stop") if opening else None
        position_id = None
        try:
            if not opening:                                   # al cerrar, MEXC recomienda decir qué posición
                theirs = [p for p in await self.client.positions(intent.symbol)
                          if int(p.get("positionType", 0)) == (1 if long_side else 2)
                          and int(float(p.get("holdVol", 0) or 0)) > 0]
                if not theirs:                                # ya la cerró el exchange (stop de emergencia) o
                    return self._reject(order, EXCHANGE_FLAT)  # el dueño a mano: el motor se sincroniza
                position_id = theirs[0].get("positionId")
                vol = min(vol, int(float(theirs[0]["holdVol"])))    # nunca cerrar más de lo que hay
                order.qty = Decimal(vol) * spec["size"]
            oid = await self.client.submit(symbol=intent.symbol, side=side_code(opening, long_side), vol=vol,
                                           price=cap, leverage=self.leverage, order_type=IOC,
                                           external_oid=_oid(intent.client_id), stop_loss=stop,
                                           position_id=position_id)
        except (TradingError, ValueError, PermissionError) as e:
            return self._reject(order, f"exchange:{str(e)[:160]}")
        order.order_id = oid
        self._emit("accepted", order)
        state = await self._wait(oid)
        await self._record_fills(order, inst, spec, oid, intent.side)
        if order.filled_qty and int(state.get("state", 0)) == FILLED:
            order.status = OrderStatus.FILLED
        elif order.filled_qty:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status, order.reason = OrderStatus.CANCELED, "ioc_sin_ejecucion"
        order.updated_at = self.clock.time()
        if order.filled_qty:
            self.stats["llenadas"] += 1
            self.balance = None                               # cambió el saldo: se relee la próxima vez
            self._emit("filled", order)
        return order

    async def _wait(self, oid: str) -> dict:
        state: dict = {}
        for _ in range(POLL_TRIES):
            try:
                state = await self.client.order(oid)
            except TradingError:
                state = {}
            if int(state.get("state", 0) or 0) in DONE_STATES:
                return state
            await asyncio.sleep(POLL_S)
        return state

    async def _record_fills(self, order: Order, inst, spec: dict, oid: str, side: Side) -> None:
        try:
            deals = await self.client.deals(oid)
        except TradingError as e:
            log.error("no pude leer las ejecuciones de la orden real %s: %s", oid, e)
            self.blocked = "ejecuciones_sin_leer"              # la conciliación decide después
            return
        for d in deals:
            qty = Decimal(str(d.get("vol", 0))) * spec["size"]
            price = Decimal(str(d.get("price", 0)))
            if qty <= 0 or price <= 0:
                continue
            fee = abs(Decimal(str(d.get("fee", 0) or 0)))
            realized = self.portfolio.apply_fill(inst, side, qty, price, fee)
            slip = None
            if order.ref_price:
                slip = (price - order.ref_price) / order.ref_price * BPS * side.sign
            f = Fill(oid, order.intent.client_id, order.intent.symbol, side, qty, price, fee,
                     "taker" if d.get("isTaker", True) else "maker", float(d.get("timestamp", 0) or 0) / 1000 or
                     self.clock.time(), realized, slip)
            prev = order.filled_qty
            order.filled_qty += qty
            order.avg_price = ((order.avg_price or Decimal(0)) * prev + price * qty) / order.filled_qty
            order.fees += fee
            order.fills.append(f)
            self._emit("fill", order, f)

    async def cancel(self, client_id: str, reason: str = "cancelada") -> Order | None:
        o = self.orders.get(client_id)
        if o is None or o.status not in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
            return o
        try:
            await self.client.cancel([o.order_id])
        except TradingError as e:
            log.warning("no pude cancelar %s: %s", client_id, e)
        o.status, o.reason = OrderStatus.CANCELED, reason
        self._emit("canceled", o)
        return o

    # -- conciliación -------------------------------------------------------------------------------------
    async def reconcile(self) -> str:
        """Compara las posiciones del exchange con las del motor. Si no coinciden, bloquea entradas y lo dice."""
        try:
            live = await self.client.positions()
            await self.account(fresh=True)
        except TradingError as e:
            self.blocked = "exchange_sin_respuesta"
            return f"sin respuesta: {str(e)[:120]}"
        theirs: dict[str, Decimal] = {}
        for p in live:
            sym = p.get("symbol", "")
            spec = await self._spec(sym)
            sign = 1 if int(p.get("positionType", 1)) == 1 else -1
            theirs[sym] = theirs.get(sym, Decimal(0)) + Decimal(str(p.get("holdVol", 0) or 0)) * spec["size"] * sign
        ours = {s: p.qty for s, p in self.portfolio.positions.items() if p.qty}
        diff = sorted(s for s in set(theirs) | set(ours) if theirs.get(s, Decimal(0)) != ours.get(s, Decimal(0)))
        self.blocked = f"diferencias:{','.join(diff)}" if diff else ""
        return self.blocked


def _oid(client_id: str) -> str:
    """El id propio de la orden para el exchange, derivado del de la intención: la misma intención nunca son dos
    órdenes (D-1). Alfanumérico y corto, como pide el exchange."""
    clean = "".join(c for c in client_id if c.isalnum())
    return clean[-32:] or "o"
