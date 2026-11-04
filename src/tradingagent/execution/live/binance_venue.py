"""Venue REAL de Binance Futures USDⓈ-M: la misma forma que el de práctica y el de MEXC (`submit` / `cancel`), así
el núcleo de decisión y de riesgo es exactamente el mismo (C-3). Ruta protegida (G-2).

Antes de CADA entrada (salir siempre se puede: A-2):
- el armado local para Binance tiene que estar vigente (`arming.status`: archivo del dueño, misma clave, prueba sin
  riesgo aprobada con esa clave, sin KILL) y permitir el activo;
- la conciliación no tiene que haber encontrado diferencias con el exchange (ni una posición sin su stop);
- el margen de lo nuevo no puede pasar el saldo DISPONIBLE de la cuenta de futuros.

Cómo opera:
- Entradas y salidas: órdenes LIMIT **IOC con tope de precio** (nunca a mercado sin tope: D-2). Antes de la primera
  entrada en cada contrato se lo pone en margen aislado y con el apalancamiento del sobre (Binance arranca los
  contratos en 20x).
- Las salidas son "solo reducir" (`reduceOnly`): nunca agrandan ni dan vuelta una posición, y nunca cierran más de lo
  que Binance tiene abierto. Si Binance ya no la tiene (la cerró el stop de emergencia, o el dueño a mano), no se
  manda nada y el motor se sincroniza (`EXCHANGE_FLAT`).
- Al llenarse una entrada se pone el **stop de emergencia** en el exchange, por el servicio Algo: STOP_MARKET que
  cierra TODA la posición, disparado por el precio de marca (`intent.tags["emergency_stop"]`, a 1,5 veces la
  distancia del stop del plan), por si la PC se apaga. Al quedar sin posición se cancela. Se guarda en disco, y la
  conciliación (cada 30 s) lo repone si falta y cancela el que quedó sin posición.
- Las ejecuciones (precio, cantidad, comisión) salen de `userTrades`; si todavía no aparecen, de la orden misma con
  la comisión de taker estimada.
- Un 503 o un corte al mandar una orden NO es una falla: la orden pudo entrar. Se consulta por el id propio
  (una intención = una orden: D-1) antes de seguir.
"""
import asyncio
import logging
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from ... import paths
from ..orders import EXCHANGE_FLAT, Fill, Order, OrderEvent, OrderIntent, OrderStatus, Side
from . import arming
from .binance_futures import BUY, DONE, IOC, SELL, BinanceClient, BinanceError

log = logging.getLogger("tradingagent.real")
VENUE = "binance-futuros"
BPS = Decimal(10_000)
BALANCE_TTL_S = 60                        # el saldo se relee en cada conciliación, después de cada ejecución y si
MARGIN_BUFFER = Decimal("0.98")           # tiene más de 1 min; se deja 2 % para comisiones
POLL_S, POLL_TRIES = 0.25, 12             # una IOC termina enseguida: hasta 3 s para ver su estado final
FILL_TRIES = 6                            # userTrades puede tardar un instante en mostrar la ejecución
STOP_CONFIRM_TRIES = 10                   # el servicio Algo muestra la cancelación con demora (4-nov)
TIME_SYNC_S = 3600                        # la hora del servidor se vuelve a leer cada hora (recvWindow de 5 s)
TAKER_FEE = Decimal("0.0005")             # comisión estimada si Binance la cobra en otra moneda (BNB)
CANCELED = ("CANCELED", "CANCELLED", "EXPIRED", "FINISHED", "TRIGGERED", "REJECTED")


def _round(x: Decimal, unit: Decimal, up: bool) -> Decimal:
    return (x / unit).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * unit


def _oid(client_id: str) -> str:
    """El id propio de la orden para Binance, derivado del de la intención: la misma intención nunca son dos órdenes
    (D-1). Alfanumérico y de hasta 32 caracteres (Binance acepta 36)."""
    clean = "".join(c for c in client_id if c.isalnum())
    return clean[-32:] or "o"


class BinanceVenue:
    def __init__(self, client: BinanceClient, session, registry, books, portfolio, clock, *, home, api_key: str,
                 limits: dict, leverage: int = 1, state_path: Path | None = None, simulated: bool = False):
        self.client, self.session, self.registry, self.books = client, session, registry, books
        self.portfolio, self.clock, self.home, self.limits = portfolio, clock, home, limits
        self._key, self.simulated = api_key, simulated
        self.leverage = max(1, min(int(leverage), int(limits["per_trade"]["max_leverage"])))
        self.state_path = Path(state_path) if state_path else None
        self.orders: dict[str, Order] = {}
        self.marks: dict[str, Decimal] = {}
        self.on_event = None
        self.blocked = ""                         # motivo de la conciliación que impide entrar ("" = ok)
        self.balance: dict | None = None          # último saldo leído de la cuenta (equity, disponible)
        self._balance_at = 0.0
        self._ready: set[str] = set()             # contratos ya en aislado y con el apalancamiento del sobre
        self._synced_at = float("-inf")
        self._seq = 0
        self.stops: dict[str, dict] = {}          # contrato → stop de emergencia {"id", "side", "trigger"}
        self.stats = {"ordenes": 0, "rechazadas": 0, "llenadas": 0, "stops_puestos": 0, "stops_cancelados": 0}
        self._load_stops()

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

    def _load_stops(self) -> None:
        data = paths.read_json(self.state_path, None) if self.state_path else None
        if isinstance(data, dict):
            self.stops = {k: v for k, v in (data.get("stops") or {}).items() if isinstance(v, dict)}

    async def _save_stops(self) -> None:
        if self.state_path is not None:
            await asyncio.to_thread(paths.write_json_atomic, self.state_path, {"stops": self.stops})

    async def _sync_time(self) -> None:
        if self.clock.time() - self._synced_at > TIME_SYNC_S:
            await self.client.sync_time()
            self._synced_at = self.clock.time()

    def open_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)]

    def summary(self) -> dict:
        return {**self.stats, "bloqueado": self.blocked, "apalancamiento": self.leverage,
                "stops": sorted(self.stops), **({"demo": True} if self.simulated else {})}

    async def account(self, fresh: bool = False) -> dict:
        """Saldo de la cuenta de futuros: {'equity', 'available'} en USDT (con caché corta)."""
        if fresh or self.balance is None or self.clock.time() - self._balance_at > BALANCE_TTL_S:
            self.balance = await self.client.usdt()
            self._balance_at = self.clock.time()
        return self.balance

    async def _exchange_qty(self, symbol: str) -> Decimal:
        """Lo que Binance tiene abierto en el contrato (con signo: + largo, − corto)."""
        return sum((Decimal(str(p.get("positionAmt", 0) or 0)) for p in await self.client.positions(symbol)
                    if p.get("symbol") == symbol), Decimal(0))

    async def _prepare(self, symbol: str) -> None:
        if symbol in self._ready:
            return
        await self.client.set_isolated(symbol)
        await self.client.set_leverage(symbol, self.leverage)
        self._ready.add(symbol)

    # -- órdenes ------------------------------------------------------------------------------------------
    async def submit(self, intent: OrderIntent) -> Order:
        now = self.clock.time()
        self._seq += 1
        order = Order(order_id=f"B{self._seq}", intent=intent, status=OrderStatus.NEW, created_at=now, qty=Decimal(0),
                      ref_price=intent.ref_price)
        self.orders[intent.client_id] = order
        self.stats["ordenes"] += 1
        opening = not intent.reduce_only
        if opening:                                           # salir siempre se puede (A-2): solo se frenan entradas
            armed, why, arm = arming.status(self.home, self._key, venue=VENUE)
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
            book = await self.books.book(intent.symbol)
        except Exception as e:                                       # noqa: BLE001 (sin datos no se opera)
            return self._reject(order, f"sin_datos:{type(e).__name__}")
        buy = intent.side is Side.BUY
        ref = book.best_ask if buy else book.best_bid
        if ref is None:
            return self._reject(order, "libro_vacio")
        top = Decimal(str(self.limits["per_trade"]["max_slippage_bps"]))
        bps = min(Decimal(str(intent.max_slippage_bps or top)), top)
        cap = _round(ref * (1 + bps / BPS) if buy else ref * (1 - bps / BPS), inst.tick_size, up=buy)
        qty = intent.qty if intent.qty is not None else (intent.notional or Decimal(0)) / ref
        try:
            await self._sync_time()
            if not opening:
                held, theirs = self.portfolio.qty(intent.symbol), await self._exchange_qty(intent.symbol)
                if held == 0 or (held > 0) == buy:
                    return self._reject(order, "reduce_only_sin_posicion")
                if theirs == 0 or (theirs > 0) == buy:        # ya la cerró el exchange (stop de emergencia) o el
                    return self._reject(order, EXCHANGE_FLAT)  # dueño a mano: el motor se sincroniza
                qty = min(qty, abs(held), abs(theirs))         # nunca cerrar más de lo que hay
        except BinanceError as e:
            return self._reject(order, f"exchange:{str(e)[:160]}")
        qty = _round(qty, inst.step_size, up=False)
        if qty <= 0 or qty < inst.min_qty:
            return self._reject(order, "menos_del_minimo")
        notional = qty * ref
        if opening:                                           # "solo reducir" no tiene nominal mínimo en Binance
            if notional < inst.min_notional:
                return self._reject(order, "nominal_menor_al_minimo")
            try:
                free = Decimal(str((await self.account())["available"]))
            except Exception as e:                                   # noqa: BLE001 (sin saldo, no se entra)
                return self._reject(order, f"sin_saldo_leido:{str(e)[:80]}")
            if notional / Decimal(self.leverage) > free * MARGIN_BUFFER:
                return self._reject(order, "supera_el_saldo_disponible")
            try:
                await self._prepare(intent.symbol)
            except BinanceError as e:
                return self._reject(order, f"exchange:margen_o_apalancamiento:{str(e)[:120]}")
        order.qty, order.limit_price = qty, cap
        cid = _oid(intent.client_id)
        try:
            res = await self.client.submit(symbol=intent.symbol, side=BUY if buy else SELL, qty=qty, price=cap,
                                           tif=IOC, client_id=cid, reduce_only=not opening)
        except BinanceError as e:
            res = await self._find(intent.symbol, cid) if e.unknown else None    # 503 o corte: pudo entrar
            if res is None:
                return self._reject(order, f"exchange:{str(e)[:160]}")
        except (ValueError, PermissionError) as e:
            return self._reject(order, f"exchange:{str(e)[:160]}")
        order.order_id = str(res.get("orderId") or cid)
        self._emit("accepted", order)
        res = await self._final(intent.symbol, cid, res)
        await self._record_fills(order, inst, intent.symbol, res, intent.side)
        if order.filled_qty and res.get("status") == "FILLED":
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
            if opening:
                await self._place_stop(intent.symbol, intent.tags.get("emergency_stop"), long=buy)
            elif self.portfolio.qty(intent.symbol) == 0:
                await self._drop_stop(intent.symbol)
        return order

    async def _find(self, symbol: str, cid: str) -> dict | None:
        """¿Entró la orden? (después de un 503 o un corte). None si Binance dice que no existe."""
        for _ in range(3):
            try:
                return await self.client.order(symbol, cid) or None
            except BinanceError as e:
                if e.code == -2013:                           # no existe: no entró
                    return None
                await asyncio.sleep(POLL_S)
        self.blocked = "orden_sin_confirmar"                  # no se sabe: la conciliación decide
        return None

    async def _final(self, symbol: str, cid: str, res: dict) -> dict:
        for _ in range(POLL_TRIES):
            if res.get("status") in DONE:
                return res
            await asyncio.sleep(POLL_S)
            try:
                res = await self.client.order(symbol, cid) or res
            except BinanceError:
                pass
        return res

    async def _record_fills(self, order: Order, inst, symbol: str, res: dict, side: Side) -> None:
        executed = Decimal(str(res.get("executedQty", 0) or 0))
        if executed <= 0:
            return
        trades: list[dict] = []
        for i in range(FILL_TRIES):
            try:
                trades = [t for t in await self.client.fills(symbol, res.get("orderId")) or []
                          if str(t.get("orderId")) == str(res.get("orderId"))]
            except BinanceError as e:
                log.warning("no pude leer las ejecuciones de la orden real %s: %s", order.order_id, e)
                trades = []
            if sum((Decimal(str(t.get("qty", 0))) for t in trades), Decimal(0)) >= executed:
                break
            if i < FILL_TRIES - 1:
                await asyncio.sleep(POLL_S)
        rows = []
        if sum((Decimal(str(t.get("qty", 0))) for t in trades), Decimal(0)) >= executed:
            for t in trades:
                qty, price = Decimal(str(t.get("qty", 0))), Decimal(str(t.get("price", 0)))
                fee = abs(Decimal(str(t.get("commission", 0) or 0)))
                if (t.get("commissionAsset") or "USDT") != "USDT":
                    fee = qty * price * TAKER_FEE             # cobrada en BNB: se estima en USDT
                rows.append((qty, price, fee, "maker" if t.get("maker") else "taker",
                             float(t.get("time", 0) or 0) / 1000 or self.clock.time()))
        else:                                                 # todavía no aparecen: lo que dice la orden
            avg = Decimal(str(res.get("avgPrice") or 0)) or (order.limit_price or Decimal(0))
            log.warning("orden real %s: ejecuciones sin leer, uso la orden (%s a %s)", order.order_id, executed, avg)
            rows.append((executed, avg, executed * avg * TAKER_FEE, "taker", self.clock.time()))
        for qty, price, fee, liquidity, ts in rows:
            if qty <= 0 or price <= 0:
                continue
            realized = self.portfolio.apply_fill(inst, side, qty, price, fee)
            slip = None
            if order.ref_price:
                slip = (price - order.ref_price) / order.ref_price * BPS * side.sign
            f = Fill(order.order_id, order.intent.client_id, symbol, side, qty, price, fee, liquidity, ts, realized, slip)
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
            await self.client.cancel(o.intent.symbol, _oid(client_id))
        except BinanceError as e:
            log.warning("no pude cancelar %s: %s", client_id, e)
        o.status, o.reason = OrderStatus.CANCELED, reason
        self._emit("canceled", o)
        return o

    # -- stop de emergencia (servicio Algo) -----------------------------------------------------------------
    async def _place_stop(self, symbol: str, trigger, *, long: bool) -> bool:
        side = SELL if long else BUY
        if not trigger:
            log.error("entrada real en %s sin stop de emergencia calculado", symbol)
            self.blocked = f"sin_stop:{symbol}"
            return False
        old = self.stops.get(symbol) or {}
        if old.get("id"):                                     # nunca dos stops de cierre en el mismo contrato
            await self._cancel_stop_id(old["id"], symbol)
        sid = _oid(f"es{symbol}{int(self.clock.time() * 1000)}")
        self.stops[symbol] = {"id": None, "side": side, "trigger": str(trigger)}
        try:
            await self.client.emergency_stop(symbol=symbol, side=side, trigger_price=trigger, client_id=sid)
        except BinanceError as e:
            log.error("no pude poner el stop de emergencia de %s: %s", symbol, e)
            if e.unknown:                                     # pudo entrar: la conciliación lo adopta si está
                self.stops[symbol]["id"] = sid
            self.blocked = f"sin_stop:{symbol}"
            await self._save_stops()
            return False
        self.stops[symbol]["id"] = sid
        self.stats["stops_puestos"] += 1
        await self._save_stops()
        return True

    async def _cancel_stop_id(self, sid: str, symbol: str) -> bool:
        """Cancela un stop y confirma que Binance lo muestra cancelado (lo muestra con demora). Al cerrarse la
        posición, Binance ya lo vence solo (EXPIRED, visto en la demo el 4-nov): entonces el pedido de cancelación
        contesta -2011 "Unknown order" y no hay nada que hacer."""
        try:
            await self.client.cancel_stop(sid)
        except BinanceError as e:
            if e.code != -2011:
                log.warning("cancelar el stop %s de %s: %s", sid, symbol, e)
        for _ in range(STOP_CONFIRM_TRIES):
            try:
                status = (await self.client.stop(sid)).get("algoStatus")
            except BinanceError:
                status = None
            if status in CANCELED:
                return True
            await asyncio.sleep(POLL_S)
        try:                                                  # último recurso: todos los stops de ese contrato
            await self.client.cancel_all_stops(symbol)
            return True
        except BinanceError as e:
            log.error("no pude cancelar los stops de %s: %s", symbol, e)
            return False

    async def _drop_stop(self, symbol: str) -> None:
        st = self.stops.get(symbol)
        if st is None:
            return
        done = await self._cancel_stop_id(st["id"], symbol) if st.get("id") else True
        if done:
            self.stops.pop(symbol, None)
            self.stats["stops_cancelados"] += 1
        await self._save_stops()

    async def _ensure_stop(self, symbol: str, amt: Decimal) -> bool:
        """Toda posición abierta en el exchange tiene su stop de emergencia; si falta, se repone."""
        side = SELL if amt > 0 else BUY
        try:
            live = [s for s in await self.client.open_stops(symbol) if s.get("symbol", symbol) == symbol
                    and s.get("side") == side]
        except BinanceError:
            return False
        if live:
            st = self.stops.get(symbol) or {}
            if st.get("id") != live[0].get("clientAlgoId"):
                self.stops[symbol] = {"id": live[0].get("clientAlgoId"), "side": side,
                                      "trigger": str(live[0].get("triggerPrice") or st.get("trigger") or "")}
                await self._save_stops()
            return True
        st = self.stops.get(symbol) or {}
        if not st.get("trigger") or st.get("side") != side:   # no se sabe dónde ponerlo: lo decide el dueño
            return False
        return await self._place_stop(symbol, st["trigger"], long=amt > 0)

    # -- conciliación -------------------------------------------------------------------------------------
    async def reconcile(self) -> str:
        """Compara las posiciones del exchange con las del motor y revisa los stops de emergencia. Si algo no
        coincide, bloquea entradas y lo dice (salir siempre se puede)."""
        try:
            await self._sync_time()
            theirs = {}
            for p in await self.client.positions():
                amt = Decimal(str(p.get("positionAmt", 0) or 0))
                if amt:
                    theirs[p.get("symbol", "")] = theirs.get(p.get("symbol", ""), Decimal(0)) + amt
            await self.account(fresh=True)
        except Exception as e:                                       # noqa: BLE001 (sin datos, no se entra)
            self.blocked = "exchange_sin_respuesta"
            return f"sin respuesta: {str(e)[:120]}"
        ours = {s: p.qty for s, p in self.portfolio.positions.items() if p.qty}
        diff = sorted(s for s in set(theirs) | set(ours) if theirs.get(s, Decimal(0)) != ours.get(s, Decimal(0)))
        problems = [f"diferencias:{','.join(diff)}"] if diff else []
        missing = [s for s, amt in sorted(theirs.items()) if not await self._ensure_stop(s, amt)]
        if missing:
            problems.append(f"sin_stop:{','.join(missing)}")
        for sym in [s for s in self.stops if s not in theirs]:
            await self._drop_stop(sym)                        # sin posición: el stop sobra
        self.blocked = ";".join(problems)
        return self.blocked


async def close_everything(client: BinanceClient, symbols=(), *, caps_bps=(50, 100, 200)) -> list[dict]:
    """Emergencia del dueño (`cerrar-todo`): cancela órdenes y stops y cierra TODAS las posiciones con órdenes
    "solo reducir" IOC con tope de precio (nunca a mercado sin tope: D-2), abriendo el tope de a poco si no se
    llenan. Devuelve lo que quede abierto."""
    await client.sync_time()
    info = await client.public("/fapi/v1/exchangeInfo")
    specs = {s["symbol"]: {f["filterType"]: f for f in s.get("filters", [])} for s in info.get("symbols", [])}
    open_ = {p["symbol"] for p in await client.positions()}
    for sym in sorted(open_ | set(symbols)):
        for fn in (client.cancel_all, client.cancel_all_stops):
            try:
                await fn(sym)
            except BinanceError as e:
                log.warning("cerrar-todo: cancelar en %s: %s", sym, e)
    for i, bps in enumerate(caps_bps):
        left = await client.positions()
        if not left:
            return []
        for p in left:
            sym, amt = p["symbol"], Decimal(str(p.get("positionAmt", 0) or 0))
            f = specs.get(sym) or {}
            tick = Decimal(str((f.get("PRICE_FILTER") or {}).get("tickSize", "0.0001")))
            step = Decimal(str((f.get("LOT_SIZE") or {}).get("stepSize", "0.001")))
            book = await client.public("/fapi/v1/ticker/bookTicker", {"symbol": sym})
            sell = amt > 0
            ref = Decimal(str(book["bidPrice"] if sell else book["askPrice"]))
            cap = _round(ref * (1 - Decimal(bps) / BPS) if sell else ref * (1 + Decimal(bps) / BPS), tick, up=not sell)
            qty = _round(abs(amt), step, up=False)
            if qty <= 0:
                continue
            try:
                await client.submit(symbol=sym, side=SELL if sell else BUY, qty=qty, price=cap, tif=IOC,
                                    client_id=f"cierre{i}{sym}"[:36], reduce_only=True)
            except BinanceError as e:
                log.warning("cerrar-todo: cerrar %s: %s", sym, e)
    return await client.positions()
