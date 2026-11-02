"""Servicio de ejecución: la ÚNICA puerta entre las decisiones y el venue (REGLAS D-0).

La inteligencia y el riesgo encolan `OrderIntent` con `submit()` (no bloquea); el servicio las ejecuta
con concurrencia acotada y publica cada evento de orden en el bus ("orders"), el diario y las alertas.
Antes de ejecutar aplica los frenos que no dependen de ninguna estrategia:
- interruptor de emergencia (archivo KILL): solo pasan órdenes reduce_only (salir siempre se puede);
- tope de órdenes por minuto (un error de lógica no puede inundar el exchange);
- cola acotada: si se llena, la intención se rechaza al instante (nunca se espera).
"""
import asyncio
import logging
from collections import deque
from decimal import Decimal
from pathlib import Path

from ..alerts.model import risk_alert, system_alert, trade_alert
from .orders import OrderEvent, OrderIntent

log = logging.getLogger("tradingagent.execution")
TOPIC_ORDERS = "orders"
RISK_ROLES = {"stop_loss", "take_profit"}


class ExecutionService:
    def __init__(self, venue, bus, clock, *, kill_file: Path | None = None, journal=None, alerts=None,
                 mode: str = "PAPER", max_queue: int = 100, max_orders_per_min: int = 30, max_concurrent: int = 4):
        self.venue, self.bus, self.clock = venue, bus, clock
        self.kill_file, self.journal, self.alerts, self.mode = kill_file, journal, alerts, mode
        self.max_per_min = max_orders_per_min
        self.queue: asyncio.Queue = asyncio.Queue(max_queue)
        self._sent: deque[float] = deque()
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task] = set()
        self.counts = {"encoladas": 0, "rechazadas": 0, "ejecutadas": 0}
        venue.on_event = self.on_venue_event

    def killed(self) -> bool:
        return bool(self.kill_file and self.kill_file.exists())

    def submit(self, intent: OrderIntent) -> tuple[bool, str]:
        """Encola sin esperar. (aceptada, motivo)."""
        reason = ""
        now = self.clock.time()
        while self._sent and now - self._sent[0] >= 60:
            self._sent.popleft()
        if self.killed() and not intent.reduce_only:
            reason = "interruptor_de_emergencia"
        elif len(self._sent) >= self.max_per_min:
            reason = "tope_de_ordenes_por_minuto"
        else:
            try:
                self.queue.put_nowait(intent)
            except asyncio.QueueFull:
                reason = "cola_de_ejecucion_llena"
        if reason:
            self.counts["rechazadas"] += 1
            log.warning("intención %s rechazada: %s", intent.client_id, reason)
            if self.journal:
                self.journal.record("intent_rejected", {"client_id": intent.client_id, "symbol": intent.symbol,
                                                        "reason": reason, "tags": intent.tags})
            return False, reason
        self._sent.append(now)
        self.counts["encoladas"] += 1
        return True, ""

    async def _execute(self, intent: OrderIntent) -> None:
        async with self._sem:
            try:
                await self.venue.submit(intent)
                self.counts["ejecutadas"] += 1
            except Exception:                                        # se informa y se sigue
                log.exception("falló la ejecución de %s", intent.client_id)
                if self.alerts:
                    self.alerts.submit(system_alert(f"Error al ejecutar {intent.client_id} ({intent.symbol}): "
                                                    "revisar el log", silent=False))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                intent = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            t = asyncio.create_task(self._execute(intent), name=f"exec-{intent.client_id}")
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=10)

    def on_venue_event(self, ev: OrderEvent) -> None:
        self.bus.publish(TOPIC_ORDERS, ev)
        if self.journal:
            data = {"event": ev.kind, "order": ev.order}
            if ev.fill:
                f = ev.fill
                data["fill"] = {"qty": str(f.qty), "price": str(f.price), "fee": str(f.fee), "liquidity": f.liquidity,
                                "realized_pnl": str(f.realized_pnl),
                                "slippage_bps": str(f.slippage_bps) if f.slippage_bps is not None else None}
            self.journal.record("order", data)
        if self.alerts is None:
            return
        if ev.kind == "filled":
            o = self.venue.orders.get(ev.order["client_id"])
            if o is None:
                return
            role = o.intent.tags.get("role", "entry")
            pnl = sum((f.realized_pnl for f in o.fills), Decimal(0)) if o.fills else None
            if role in RISK_ROLES:
                self.alerts.submit(risk_alert(role, o.intent.symbol, o.intent.side, o.filled_qty, o.avg_price, pnl,
                                              mode=self.mode))
            else:
                self.alerts.submit(trade_alert(o.intent.symbol, o.intent.side, o.filled_qty, o.avg_price,
                                               why=o.intent.tags.get("why", ""), impact=o.intent.tags.get("impact"),
                                               confidence=o.intent.tags.get("confidence"),
                                               source=o.intent.tags.get("source"), role=role, pnl=pnl,
                                               mode=self.mode))
        elif ev.kind == "rejected" and ev.order.get("role") in RISK_ROLES:
            self.alerts.submit(system_alert(f"⚠️ Orden de protección rechazada: {ev.order['symbol']} "
                                            f"({ev.order['role']}): {ev.order['reason']}", silent=False))
