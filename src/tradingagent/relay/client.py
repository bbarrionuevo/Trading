"""Relé de Tokio, lado del agente: manda un latido firmado cada 10 s al relé y recibe los avisos que el relé leyó desde
Asia. Cada aviso entra a la ingesta como si lo hubiera leído el agente, con la MISMA fuente y el mismo id: el que
llegue primero (el relé o el lector propio) se procesa y el otro es un duplicado. Las reglas, el riesgo y la orden
siguen siendo los de siempre.

Dos lugares, el mismo cliente:
- En la PC (el relé está en Tokio, detrás del NAT): el latido abre y mantiene el túnel y lleva si la PC está en
  pausa (`kill`: el archivo KILL; se manda apenas cambia) para que el relé frene también al agente de Tokio. Recibe
  la telemetría del agente de Tokio ("tel") y la pasa a `on_telemetry` (el tablero, relay/tablero.py).
- En el servidor de Tokio (el relé en 127.0.0.1): recibe los avisos en microsegundos y manda su telemetría con
  `send` (telemetry/remota.py).

Se prende con TRADING_HOME/relay.json ({"host": ..., "port": 50005}) o RELAY_HOST/RELAY_PORT, y la clave en
TRADING_HOME/relay.key.
"""
import asyncio
import logging
from collections import Counter
from pathlib import Path

from ..core.events import NewsItem
from ..ingestion.catalog import DEFAULT_SOURCES
from ..ingestion.sources import SourceHealth
from . import protocol

log = logging.getLogger("tradingagent.relay")
HEARTBEAT_S = 10.0
KILL_CHECK_S = 1.0                                  # la pausa de la PC llega al relé en ~1 s, no en 10
SPECS = {s.id: s for s in DEFAULT_SOURCES}
ALLOWED = ("upbit.avisos", "binance.listados")     # solo avisos oficiales que también lee la PC
SOURCE_ID = "relay.tokio"


class RelayClient(asyncio.DatagramProtocol):
    def __init__(self, host: str, port: int, key: bytes, on_item, clock, heartbeat_s: float = HEARTBEAT_S, *,
                 kill_file: Path | None = None, on_telemetry=None):
        self.host, self.port, self.key, self.on_item, self.clock = host, port, key, on_item, clock
        self.heartbeat_s, self.kill_file, self.on_telemetry = heartbeat_s, kill_file, on_telemetry
        self.seen = protocol.NonceCache()
        self.transport = None
        self.counts: Counter = Counter()
        self.rtt_ms: float | None = None
        self.health = SourceHealth()
        self._tasks: set[asyncio.Task] = set()

    # -- UDP ---------------------------------------------------------------------------------------
    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        now = self.clock.time()
        msg = protocol.decode(data, self.key, self.seen, now)
        if msg is None:
            self.counts["rechazados"] += 1
            return
        kind = msg.get("t")
        if kind == "ack":
            self.counts["acks"] += 1
            self.rtt_ms = round((now - float(msg.get("eco") or now)) * 1000, 1)
            self.health.state, self.health.last_ok, self.health.consecutive_failures = "ok", now, 0
        elif kind == "sig":
            item = self.to_item(msg, now)
            if item is None:
                self.counts["ignorados"] += 1
                return
            self.counts["avisos"] += 1
            self.health.items += 1
            spec = SPECS[item.source]
            self._spawn(self.on_item(item, max_age_s=spec.max_age_s), f"rele-{item.id[:8]}")
            log.info("aviso por el relé (%s, %.1f s de publicado): %s", item.source,
                     now - (item.published_at or now), item.title[:100])
        elif kind == "tel" and self.on_telemetry is not None:
            self.counts["telemetria"] += 1
            self._spawn(self.on_telemetry(msg), "rele-telemetria")

    def _spawn(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def error_received(self, exc) -> None:
        self.counts["errores"] += 1

    def to_item(self, msg: dict, now: float) -> NewsItem | None:
        """El aviso como lo habría leído la PC: misma fuente, mismo id (deduplica con el lector propio), el nivel y la
        categoría del catálogo de la PC (no los que diga el datagrama). Solo fuentes de la lista."""
        src = str(msg.get("src") or "")
        spec = SPECS.get(src)
        if src not in ALLOWED or spec is None or not msg.get("id") or not msg.get("title"):
            return None
        pub = msg.get("pub")
        return NewsItem(id=str(msg["id"]), source=src, tier=spec.tier, kind="relay", title=str(msg["title"])[:500],
                        body=str(msg.get("body") or "")[:2000], url=str(msg.get("url") or ""),
                        published_at=float(pub) if pub else None, received_at=now,
                        assets_hint=spec.assets, category_hint=spec.category)

    def send(self, payload: dict) -> bool:
        """Manda un mensaje firmado al relé (la telemetría del agente de Tokio). Si no entra en un datagrama o no hay
        conexión todavía, no se manda (la telemetría nunca frena al agente)."""
        if self.transport is None:
            self.counts["sin_conexion"] += 1
            return False
        try:
            self.transport.sendto(protocol.encode(payload, self.key, self.clock.time()))
        except (ValueError, OSError) as e:
            self.counts["no_enviados"] += 1
            log.debug("telemetría no enviada: %s", e)
            return False
        return True

    def paused(self) -> bool | None:
        return None if self.kill_file is None else self.kill_file.exists()

    def heartbeat(self) -> dict:
        kill = self.paused()
        return {"t": "hb"} if kill is None else {"t": "hb", "kill": kill}

    # -- ciclo -------------------------------------------------------------------------------------
    async def run(self, stop: asyncio.Event, session=None) -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            try:
                transport, _ = await loop.create_datagram_endpoint(lambda: self, remote_addr=(self.host, self.port))
                log.info("relé de Tokio: latidos a %s:%d", self.host, self.port)
                try:
                    await self._beat(transport, stop)
                finally:
                    transport.close()
                    self.transport = None
            except OSError as e:                                 # sin red: se reintenta (el relé nunca frena al agente)
                self.health.state = "degradada"
                log.warning("relé de Tokio: %s; reintento en 30 s", e)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30)
                except TimeoutError:
                    pass

    async def _beat(self, transport, stop: asyncio.Event) -> None:
        """Latido cada `heartbeat_s`, y uno extra apenas la PC entra o sale de pausa."""
        last_sent, last_kill = float("-inf"), None
        tick = min(KILL_CHECK_S, self.heartbeat_s)
        while not stop.is_set():
            now = self.clock.time()
            kill = await asyncio.to_thread(self.paused) if self.kill_file is not None else None
            if now - last_sent >= self.heartbeat_s or kill != last_kill:
                transport.sendto(protocol.encode(self.heartbeat() if kill is None else {"t": "hb", "kill": kill},
                                                 self.key, now))
                self.counts["latidos"] += 1
                last_sent, last_kill = now, kill
                if self.health.last_ok is not None and now - self.health.last_ok > 3 * self.heartbeat_s:
                    self.health.state = "degradada"
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick)
            except TimeoutError:
                pass

    def snapshot(self) -> dict:
        return {"rtt_ms": self.rtt_ms, **dict(self.counts)}
