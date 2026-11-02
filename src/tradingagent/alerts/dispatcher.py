"""Despachador de alertas en un HILO PROPIO con su propio bucle de eventos (REGLAS E-3).

Garantía de diseño: `submit()` es O(log n), no hace E/S y no espera nunca a Telegram. Si Telegram está
caído, lento, devuelve 429 o se cuelga, lo único que se atrasa es la cola de alertas: el bucle del motor
de trading no comparte ni una tarea ni un socket con este hilo.

- Cola con prioridad: RISK sale antes que TRADE, SYSTEM e INFO. Acotada: si se llena se descarta lo
  menos urgente (las de RISK nunca se descartan por espacio).
- Alertas con la misma `key` se fusionan (la más nueva reemplaza a la vieja en la cola).
- Ritmo: token bucket por chat (Telegram tolera ~1 mensaje/s por chat con ráfagas cortas).
- 429: espera lo que pide Telegram. Red o 5xx: espera exponencial. 401/403/404: credenciales o chat
  inválidos ⇒ se pausa el envío y se vuelve a probar cada `permanent_pause_s`.
- Vencimiento: una alerta TRADE/INFO que espera de más se descarta; las RISK se mandan con la demora.
- Prueba de inicio: getMe + mensaje de inicio, con el resultado en `startup` (el motor no lo espera).
"""
import asyncio
import heapq
import itertools
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from ..core.redact import redact
from .model import Alert, Priority

log = logging.getLogger("tradingagent.alerts")
MAX_AGE_S = {Priority.RISK: 3600.0, Priority.TRADE: 900.0, Priority.SYSTEM: 1800.0, Priority.INFO: 300.0}


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    alert: Alert = field(compare=False)
    enqueued: float = field(compare=False, default=0.0)
    attempts: int = field(compare=False, default=0)
    dead: bool = field(compare=False, default=False)


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: int, now=time.monotonic):
        self.rate, self.capacity, self.now = rate_per_s, float(burst), now
        self.tokens, self.t = float(burst), now()

    def wait_time(self) -> float:
        t = self.now()
        self.tokens = min(self.capacity, self.tokens + (t - self.t) * self.rate)
        self.t = t
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate

    def take(self) -> None:
        self.tokens -= 1


class AlertDispatcher:
    def __init__(self, transport_factory, *, max_queue: int = 500, rate_per_s: float = 1.0, burst: int = 3,
                 max_retries: int = 6, permanent_pause_s: float = 300.0, startup_alert: Alert | None = None,
                 retry_base_s: float = 2.0, name: str = "alertas"):
        self.transport_factory, self.max_queue, self.max_retries = transport_factory, max_queue, max_retries
        self.permanent_pause_s, self.startup_alert, self.name = permanent_pause_s, startup_alert, name
        self.retry_base_s = retry_base_s
        self.bucket = TokenBucket(rate_per_s, burst)
        self._heap: list[_Item] = []
        self._by_key: dict[str, _Item] = {}
        self._alive = 0
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._deadline = 0.0
        self._ready = threading.Event()
        self.startup_done = threading.Event()
        self.startup: dict = {}
        self.health = "inactivo"
        self.last_error = ""
        self.stats: Counter = Counter()
        self.submit_ns_max = 0

    # -- productor (cualquier hilo) -----------------------------------------------------------
    def submit(self, alert: Alert) -> bool:
        """Encola sin esperar. Devuelve False si la alerta se descartó (cola llena con cosas más urgentes)."""
        t0 = time.perf_counter_ns()
        accepted = True
        with self._lock:
            if self._stopping:
                accepted = False
            else:
                old = self._by_key.get(alert.key) if alert.key else None
                if old is not None and not old.dead:
                    old.dead = True
                    self._alive -= 1
                    self.stats["fusionadas"] += 1
                if self._alive >= self.max_queue and alert.priority is not Priority.RISK:
                    worst = max((i for i in self._heap if not i.dead), default=None)
                    if worst is None or worst.priority <= alert.priority:
                        accepted = False                          # lo nuevo es lo menos urgente: se descarta
                    else:
                        worst.dead = True
                        self._alive -= 1
                        self.stats["descartadas_por_espacio"] += 1
                if accepted:
                    item = _Item(int(alert.priority), next(self._seq), alert, time.monotonic())
                    heapq.heappush(self._heap, item)
                    self._alive += 1
                    if alert.key:
                        self._by_key[alert.key] = item
                    self.stats["encoladas"] += 1
                else:
                    self.stats["descartadas_por_espacio"] += 1
        loop, wake = self._loop, self._wake
        if accepted and loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:                                  # el bucle del hilo ya cerró
                pass
        self.submit_ns_max = max(self.submit_ns_max, time.perf_counter_ns() - t0)
        return accepted

    def queued(self) -> int:
        return self._alive

    # -- hilo propio ---------------------------------------------------------------------------
    def start(self, ready_timeout: float = 5.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._thread_main, name=self.name, daemon=True)
        self._thread.start()
        self._ready.wait(ready_timeout)

    def stop(self, timeout: float = 5.0) -> None:
        """Deja de aceptar alertas, intenta mandar lo pendiente (RISK primero) hasta `timeout` y cierra."""
        with self._lock:
            self._stopping = True
        self._deadline = time.monotonic() + timeout
        if self._loop is not None and self._wake is not None:
            try:
                self._loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout + 2)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as e:                                    # el hilo nunca tumba al motor
            self.health, self.last_error = "caido", redact(e)
            log.exception("el hilo de alertas terminó con error")
        finally:
            self._ready.set()
            self.startup_done.set()

    def _pop(self) -> _Item | None:
        with self._lock:
            while self._heap:
                item = heapq.heappop(self._heap)
                if item.dead:
                    continue
                self._alive -= 1
                if item.alert.key and self._by_key.get(item.alert.key) is item:
                    del self._by_key[item.alert.key]
                return item
        return None

    def _requeue(self, item: _Item) -> None:
        with self._lock:
            heapq.heappush(self._heap, item)
            self._alive += 1
            if item.alert.key and item.alert.key not in self._by_key:
                self._by_key[item.alert.key] = item

    async def _self_test(self, transport) -> None:
        t0 = time.monotonic()
        me = await transport.get_me()
        self.startup = {"ok": False, "transport": transport.name, "bot": me.error if me.ok else "",
                        "error": "" if me.ok else me.error}
        if me.ok and self.startup_alert is not None:
            r = await transport.send(self.startup_alert)
            self.startup.update(ok=r.ok, error="" if r.ok else r.error)
            self.stats["enviadas" if r.ok else "fallidas"] += 1
        elif me.ok:
            self.startup["ok"] = True
        self.startup["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        self.health = "ok" if self.startup["ok"] else ("credenciales_invalidas" if me.permanent else "fallando")
        self.last_error = self.startup.get("error", "")
        if self.startup["ok"]:
            log.info("alertas: %s listo %s (%.0f ms)", transport.name, self.startup["bot"], self.startup["latency_ms"])
        else:
            log.error("alertas: la prueba de inicio FALLÓ: %s", self.startup["error"])
        self.startup_done.set()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        transport = self.transport_factory()
        await transport.open()
        self._ready.set()
        try:
            await self._self_test(transport)
            paused_until = 0.0
            while True:
                if self._stopping and (self._alive == 0 or time.monotonic() > self._deadline):
                    break
                item = self._pop()
                if item is None:
                    if self._stopping:
                        break
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                    except TimeoutError:
                        pass
                    continue
                age = time.monotonic() - item.enqueued
                if age > MAX_AGE_S[item.alert.priority] and item.alert.priority is not Priority.RISK:
                    self.stats["vencidas"] += 1
                    continue
                now = time.monotonic()
                if now < paused_until and not self._stopping:
                    self._requeue(item)
                    await asyncio.sleep(min(1.0, paused_until - now))
                    continue
                wait = self.bucket.wait_time()
                if wait > 0:
                    self._requeue(item)
                    await asyncio.sleep(wait)
                    continue
                self.bucket.take()
                alert = item.alert
                if age > 60:                                       # avisa la demora (p. ej. tras un corte)
                    alert = Alert(alert.priority, alert.kind, alert.text + f"\n⏱ con {age / 60:.0f} min de demora",
                                  alert.key, alert.silent, alert.meta)
                r = await transport.send(alert)
                if r.ok:
                    self.stats["enviadas"] += 1
                    self.health = "ok"
                    continue
                self.stats["fallidas"] += 1
                self.last_error = r.error
                item.attempts += 1
                if r.permanent:
                    self.health = "credenciales_invalidas"
                    paused_until = time.monotonic() + self.permanent_pause_s
                    log.error("alertas: Telegram rechazó las credenciales o el chat (%s); pausa de %.0f s",
                              r.error, self.permanent_pause_s)
                    self._requeue(item)
                    continue
                self.health = "fallando"
                if r.retry_after is not None:
                    self.stats["429"] += 1
                    delay = r.retry_after + 0.5
                else:
                    delay = min(60.0, self.retry_base_s * 2.0 ** (item.attempts - 1))
                if item.attempts > self.max_retries and item.alert.priority is not Priority.RISK:
                    self.stats["abandonadas"] += 1
                    log.warning("alertas: se abandona una alerta %s tras %d intentos (%s)", item.alert.kind,
                                item.attempts, r.error)
                    continue
                self._requeue(item)
                if not self._stopping:
                    await asyncio.sleep(delay)
        finally:
            await transport.close()

    def snapshot(self) -> dict:
        with self._lock:
            stats = dict(self.stats)
        return {"health": self.health, "queued": self._alive, "stats": stats, "last_error": self.last_error,
                "submit_us_max": round(self.submit_ns_max / 1000, 1), "startup": dict(self.startup)}
