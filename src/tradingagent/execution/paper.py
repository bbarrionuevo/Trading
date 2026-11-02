"""Venue de paper trading riguroso: simula cómo se ejecutaría la orden en el exchange real.

Cómo modela la realidad (y por qué):
- Latencia: cada orden "viaja" un tiempo lognormal (mediana 80 ms) y se llena con el libro del momento
  de LLEGADA, no con el de la decisión: el precio se mueve mientras la orden viaja.
- Deslizamiento: la orden recorre los niveles del libro (VWAP) y solo cuenta con una fracción de la
  profundidad visible (`participation`): otros llegan antes. Lo que no alcanza queda parcial.
- Tope de precio obligatorio: una MARKET sin tope recibe `default_max_slippage_bps` (REGLAS D-2): en un
  libro finito una orden a mercado sin tope puede llenarse a cualquier precio.
- Límites en reposo: se llenan solo si el mercado ATRAVIESA el precio (no al tocarlo): el modelo que
  contaba los toques sobrestimó los llenados en el proyecto anterior.
- Stops (stop-loss): se disparan contra el mejor precio del lado que ejecuta y salen como límite
  agresivo (tope `stop_slippage_bps`), re-preciado en cada libro hasta completar la salida.
- Comisiones taker/maker del instrumento, reglas de tick, lote y mínimo nominal, banda de precio contra
  el "dedo gordo", saldo (spot) y margen (perp), reduce-only y OCO (stop + take-profit).
- Idempotencia: el mismo client_id devuelve la misma orden y nunca se ejecuta dos veces (REGLAS D-1).
"""
import asyncio
import logging
import math
import random
from collections import deque
from dataclasses import dataclass
from decimal import Decimal as D

from ..marketdata.book import BPS, OrderBook
from ..marketdata.providers import MarketDataError
from .instruments import InstrumentRegistry
from .orders import Fill, Order, OrderEvent, OrderIntent, OrderStatus, OrderType, Side, TimeInForce
from .portfolio import Portfolio

log = logging.getLogger("tradingagent.paper")
ZERO, ONE = D(0), D(1)


@dataclass(frozen=True)
class LatencyModel:
    median_ms: float = 80.0
    sigma: float = 0.6
    max_ms: float = 3000.0

    def sample(self, rng: random.Random) -> float:
        if self.median_ms <= 0:
            return 0.0
        return min(self.max_ms, rng.lognormvariate(math.log(self.median_ms), self.sigma)) / 1000.0


@dataclass(frozen=True)
class PaperConfig:
    latency: LatencyModel = LatencyModel()
    participation: D = D("0.8")
    max_book_age_s: float = 2.0
    default_max_slippage_bps: D = D("50")
    stop_slippage_bps: D = D("50")
    price_band_bps: D = D("500")
    limit_fill_rule: str = "through"          # through (conservador) | touch
    max_open_orders: int = 50


class PaperVenue:
    def __init__(self, registry: InstrumentRegistry, books, portfolio: Portfolio, clock,
                 config: PaperConfig | None = None, *, seed: int = 0, on_event=None, sleep=asyncio.sleep):
        self.registry, self.books, self.portfolio, self.clock = registry, books, portfolio, clock
        self.cfg = config or PaperConfig()
        self.rng = random.Random(seed)
        self.on_event, self.sleep = on_event, sleep
        self.orders: dict[str, Order] = {}
        self._open: dict[str, Order] = {}                 # límites en reposo y stops pendientes
        self._seq = 0
        self._lock = asyncio.Lock()
        self.marks: dict[str, D] = {}
        self.stats = {"orders": 0, "fills": 0, "rejected": 0, "latency_ms": deque(maxlen=10000),
                      "slippage_bps": deque(maxlen=10000)}

    # -- utilidades ---------------------------------------------------------------------------
    def _emit(self, kind: str, order: Order, fill: Fill | None = None) -> None:
        order.updated_at = self.clock.time()
        if self.on_event:
            try:
                self.on_event(OrderEvent(kind, order.snapshot(), fill, order.updated_at))
            except Exception:                                   # un consumidor no frena el venue
                log.exception("on_event falló")

    def _reject(self, order: Order, reason: str) -> Order:
        order.status, order.reason = OrderStatus.REJECTED, reason
        self._open.pop(order.intent.client_id, None)
        self.stats["rejected"] += 1
        self._emit("rejected", order)
        return order

    def _finish(self, order: Order, status: OrderStatus, reason: str = "") -> None:
        order.status, order.reason = status, reason or order.reason
        self._open.pop(order.intent.client_id, None)
        self._emit(str(status), order)

    async def _book(self, symbol: str) -> OrderBook:
        book = await self.books.book(symbol)
        problems = book.problems()
        if problems:
            raise MarketDataError("; ".join(problems))
        age = book.age(self.clock.time())
        if age > self.cfg.max_book_age_s:
            raise MarketDataError(f"libro de hace {age:.1f} s (máximo {self.cfg.max_book_age_s} s)")
        if book.mid is not None:
            self.marks[symbol] = book.mid
        return book

    def open_orders(self) -> list[Order]:
        return list(self._open.values())

    # -- validación ---------------------------------------------------------------------------
    def _static_problems(self, i: OrderIntent, inst) -> str | None:
        if inst is None:
            return "instrumento_desconocido"
        if (i.qty is None) == (i.notional is None):
            return "indicar_qty_o_notional"
        if (i.qty is not None and i.qty <= 0) or (i.notional is not None and i.notional <= 0):
            return "cantidad_no_positiva"
        if i.type is OrderType.LIMIT and (i.limit_price is None or i.limit_price <= 0):
            return "limite_sin_precio"
        if i.type is OrderType.STOP_MARKET and (i.stop_price is None or i.stop_price <= 0):
            return "stop_sin_precio"
        if i.notional is not None and i.notional < inst.min_notional:
            return "nominal_menor_al_minimo"
        if len(self._open) >= self.cfg.max_open_orders and i.tif is TimeInForce.GTC:
            return "demasiadas_ordenes_abiertas"
        return None

    def _qty_for(self, i: OrderIntent, inst, book: OrderBook) -> D:
        if i.qty is not None:
            return inst.floor_qty(i.qty)
        price = book.best_ask if i.side is Side.BUY else book.best_bid
        return inst.floor_qty(i.notional / price) if price else ZERO

    def _cap(self, i: OrderIntent, inst, ref: D, bps: D) -> D:
        if i.side is Side.BUY:
            return inst.round_price(ref * (ONE + bps / BPS), up=False)    # nunca más laxo que lo pedido
        return inst.round_price(ref * (ONE - bps / BPS), up=True)

    # -- ejecución ----------------------------------------------------------------------------
    def _cannot(self, order: Order, reason: str) -> None:
        """Sin saldo o margen: si todavía no se llenó nada es un rechazo; si no, se cancela el resto."""
        if order.filled_qty == 0:
            self._reject(order, reason)
        else:
            self._finish(order, OrderStatus.CANCELED, reason)

    def _margin_ok(self, inst, new_pos: D, price: D) -> bool:
        """Margen de TODAS las posiciones perp (con la nueva) ≤ equity."""
        need = abs(new_pos) * self.marks.get(inst.symbol, price) / inst.max_leverage
        for sym, p in self.portfolio.positions.items():
            other = self.registry.get(sym)
            if sym != inst.symbol and p.qty and other is not None and other.kind == "perp":
                need += abs(p.qty) * self.marks.get(sym, p.avg_price) / other.max_leverage
        return need <= self.portfolio.equity(self.marks)

    def _fill(self, order: Order, inst, qty: D, price: D, liquidity: str) -> Fill | None:
        i = order.intent
        qty = inst.floor_qty(min(qty, order.remaining))
        if i.reduce_only:
            pos = self.portfolio.qty(inst.symbol)
            if pos == 0 or (pos > 0) == (i.side is Side.BUY):
                self._finish(order, OrderStatus.CANCELED, "reduce_only_sin_posicion")
                return None
            qty = min(qty, abs(pos))
        if qty <= 0 or qty < inst.min_qty:
            return None
        notional = qty * price
        fee = inst.fee(notional, liquidity)
        current = self.portfolio.qty(inst.symbol)
        if inst.kind == "spot":
            if i.side is Side.BUY and self.portfolio.cash < notional + fee:
                self._cannot(order, "saldo_insuficiente")
                return None
            if i.side is Side.SELL and current < qty:
                self._cannot(order, "posicion_insuficiente_spot")
                return None
        else:
            new_pos = current + qty * i.side.sign
            if abs(new_pos) > abs(current) and not self._margin_ok(inst, new_pos, price):
                self._cannot(order, "margen_insuficiente")
                return None
        realized = self.portfolio.apply_fill(inst, i.side, qty, price, fee)
        slip = None
        if liquidity == "taker" and order.ref_price:
            slip = (price - order.ref_price) / order.ref_price * BPS * i.side.sign      # + = peor que lo visto
            self.stats["slippage_bps"].append(float(slip))
        f = Fill(order.order_id, i.client_id, i.symbol, i.side, qty, price, fee, liquidity, self.clock.time(),
                 realized, slip)
        prev = order.filled_qty
        order.filled_qty += qty
        order.avg_price = ((order.avg_price or ZERO) * prev + price * qty) / order.filled_qty
        order.fees += fee
        order.fills.append(f)
        done = order.remaining <= 0 or order.remaining < inst.min_qty
        order.status = OrderStatus.FILLED if done else OrderStatus.PARTIALLY_FILLED
        self.stats["fills"] += 1
        self._emit("fill", order, f)
        if done:
            self._open.pop(i.client_id, None)
            self._emit("filled", order)
        self._oco_after_fill(order, qty)
        return f

    def _take(self, order: Order, inst, book: OrderBook, cap: D | None) -> None:
        """Toma liquidez (taker) hasta el tope."""
        side = str(order.intent.side)
        w = book.walk(side, qty=order.remaining, limit_price=cap, participation=self.cfg.participation)
        if w.filled_qty > 0:
            self._fill(order, inst, w.filled_qty, w.cost / w.filled_qty, "taker")

    def _oco_after_fill(self, order: Order, qty: D) -> None:
        g = order.intent.oco_group
        if not g:
            return
        for sib in [o for o in self._open.values() if o.intent.oco_group == g and o is not order]:
            sib.qty -= qty                                         # el hermano protege lo que queda
            if order.status is OrderStatus.FILLED or sib.qty <= sib.filled_qty:
                self._finish(sib, OrderStatus.CANCELED, f"oco: se ejecutó {order.intent.client_id}")

    def _through(self, order: Order, book: OrderBook) -> D:
        """Cantidad que el mercado le habría llenado a un límite en reposo (solo si lo atraviesa)."""
        i, lim = order.intent, order.limit_price
        touch = self.cfg.limit_fill_rule == "touch"
        if i.side is Side.BUY:
            levels = [(p, q) for p, q in book.asks if p < lim or (touch and p == lim)]
        else:
            levels = [(p, q) for p, q in book.bids if p > lim or (touch and p == lim)]
        return sum((q for _, q in levels), ZERO) * self.cfg.participation

    def _stop_triggered(self, order: Order, book: OrderBook) -> bool:
        i = order.intent
        if i.side is Side.SELL:
            return book.best_bid is not None and book.best_bid <= i.stop_price
        return book.best_ask is not None and book.best_ask >= i.stop_price

    def _work(self, order: Order, inst, book: OrderBook) -> None:
        """Procesa una orden en reposo contra un libro nuevo."""
        i = order.intent
        if order.status.is_final:
            return
        if i.expire_at is not None and self.clock.time() >= i.expire_at:
            self._finish(order, OrderStatus.EXPIRED, "vencida")
            return
        if i.type is OrderType.LIMIT:
            avail = self._through(order, book)
            if avail > 0:
                self._fill(order, inst, avail, order.limit_price, "maker")
        elif i.type is OrderType.STOP_MARKET:
            if not order.triggered:
                if not self._stop_triggered(order, book):
                    return
                order.triggered = True
                self._emit("triggered", order)
            best = book.best_bid if i.side is Side.SELL else book.best_ask
            if best is not None:
                self._take(order, inst, book, self._cap(i, inst, best, i.max_slippage_bps or self.cfg.stop_slippage_bps))

    # -- API ----------------------------------------------------------------------------------
    async def submit(self, intent: OrderIntent) -> Order:
        if intent.client_id in self.orders:                         # idempotencia
            return self.orders[intent.client_id]
        self._seq += 1
        self.stats["orders"] += 1
        inst = self.registry.get(intent.symbol)
        order = Order(f"P{self._seq:08d}", intent, OrderStatus.NEW, self.clock.time(), qty=ZERO)
        self.orders[intent.client_id] = order
        problem = self._static_problems(intent, inst)
        if problem:
            return self._reject(order, problem)
        try:
            decision_book = await self._book(intent.symbol)           # lo que ve quien envía
        except MarketDataError as e:
            return self._reject(order, f"sin_datos: {e}")
        order.qty = self._qty_for(intent, inst, decision_book)
        if order.qty <= 0 or order.qty < inst.min_qty:
            return self._reject(order, "cantidad_menor_al_minimo")
        ref = intent.ref_price or (decision_book.best_ask if intent.side is Side.BUY else decision_book.best_bid)
        order.ref_price = ref
        if intent.notional is None and order.qty * ref < inst.min_notional:
            return self._reject(order, "nominal_menor_al_minimo")
        if intent.type is OrderType.LIMIT:
            # compra al tick de abajo, venta al de arriba: el redondeo nunca empeora el precio pedido
            order.limit_price = inst.round_price(intent.limit_price, up=intent.side is Side.SELL)
            mid = decision_book.mid
            if mid and abs(order.limit_price - mid) / mid * BPS > self.cfg.price_band_bps:
                return self._reject(order, "precio_fuera_de_banda")
        self._emit("accepted", order)
        latency = self.cfg.latency.sample(self.rng)
        self.stats["latency_ms"].append(latency * 1000)
        await self.sleep(latency)                                   # la orden viaja al exchange
        async with self._lock:
            try:
                book = await self._book(intent.symbol)                # lo que encuentra al llegar
            except MarketDataError as e:
                return self._reject(order, f"sin_datos_al_llegar: {e}")
            if intent.type is OrderType.MARKET:
                self._take(order, inst, book, self._cap(intent, inst, ref,
                                                        intent.max_slippage_bps or self.cfg.default_max_slippage_bps))
                if not order.status.is_final:
                    self._finish(order, OrderStatus.CANCELED if order.filled_qty else OrderStatus.EXPIRED,
                                 "ioc: sin liquidez dentro del tope" if order.filled_qty == 0 else "ioc: resto cancelado")
            elif intent.type is OrderType.LIMIT:
                self._take(order, inst, book, order.limit_price)     # la parte que cruza es taker
                if not order.status.is_final:
                    if intent.tif is TimeInForce.IOC:
                        self._finish(order, OrderStatus.CANCELED if order.filled_qty else OrderStatus.EXPIRED,
                                     "ioc: resto cancelado")
                    else:
                        self._open[intent.client_id] = order
            else:                                                    # STOP_MARKET
                self._open[intent.client_id] = order
                self._work(order, inst, book)                         # quizá ya está disparado (gap)
        return order

    async def cancel(self, client_id: str, reason: str = "cancelada") -> Order | None:
        async with self._lock:
            o = self._open.get(client_id)
            if o is None or o.status.is_final:
                return o or self.orders.get(client_id)
            self._finish(o, OrderStatus.CANCELED, reason)
            return o

    async def on_book(self, book: OrderBook) -> None:
        """Libro nuevo de un símbolo: trabaja sus órdenes en reposo y stops."""
        if book.mid is not None:
            self.marks[book.symbol] = book.mid
        async with self._lock:
            for o in [o for o in self._open.values() if o.intent.symbol == book.symbol]:
                self._work(o, self.registry.get(book.symbol), book)

    async def poll_open_orders(self) -> None:
        """Pide libros frescos de los símbolos con órdenes en reposo (modo en vivo)."""
        for sym in {o.intent.symbol for o in self._open.values()}:
            try:
                await self.on_book(await self._book(sym))
            except MarketDataError as e:
                log.warning("sin libro para %s: %s (las órdenes en reposo esperan)", sym, e)

    async def run_monitor(self, stop: asyncio.Event, interval_s: float = 1.0) -> None:
        while not stop.is_set():
            if self._open:
                await self.poll_open_orders()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass

    def summary(self) -> dict:
        lat, slip = self.stats["latency_ms"], self.stats["slippage_bps"]
        return {"orders": self.stats["orders"], "fills": self.stats["fills"], "rejected": self.stats["rejected"],
                "open": len(self._open), "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else None,
                "slippage_bps_avg": round(sum(slip) / len(slip), 2) if slip else None,
                "portfolio": self.portfolio.snapshot(self.marks)}
