"""Relé de Tokio, lado de la PC (Tier-1): manda un latido firmado cada 10 s al servidor de Tokio (abre y mantiene el
túnel del NAT, aunque la IP de la PC cambie o esté detrás de CGNAT) y recibe los avisos que el servidor leyó desde
Asia. Cada aviso entra a la ingesta como si lo hubiera leído la PC, con la MISMA fuente y el mismo id: el que llegue
primero (el relé o el lector de la PC) se procesa y el otro es un duplicado. Las reglas, el riesgo y la orden siguen
siendo los de siempre (~30 ms de la llegada a la orden).

Se prende con RELAY_HOST (y RELAY_PORT, por defecto 50005) en el .env y la clave en TRADING_HOME/relay.key.
"""
import asyncio
import logging
from collections import Counter

from ..core.events import NewsItem
from ..ingestion.catalog import DEFAULT_SOURCES
from ..ingestion.sources import SourceHealth
from . import protocol

log = logging.getLogger("tradingagent.relay")
HEARTBEAT_S = 10.0
SPECS = {s.id: s for s in DEFAULT_SOURCES}
ALLOWED = ("upbit.avisos", "binance.listados")     # solo avisos oficiales que también lee la PC
SOURCE_ID = "relay.tokio"


class RelayClient(asyncio.DatagramProtocol):
    def __init__(self, host: str, port: int, key: bytes, on_item, clock, heartbeat_s: float = HEARTBEAT_S):
        self.host, self.port, self.key, self.on_item, self.clock = host, port, key, on_item, clock
        self.heartbeat_s = heartbeat_s
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
        if msg.get("t") == "ack":
            self.counts["acks"] += 1
            self.rtt_ms = round((now - float(msg.get("eco") or now)) * 1000, 1)
            self.health.state, self.health.last_ok, self.health.consecutive_failures = "ok", now, 0
        elif msg.get("t") == "sig":
            item = self.to_item(msg, now)
            if item is None:
                self.counts["ignorados"] += 1
                return
            self.counts["avisos"] += 1
            self.health.items += 1
            spec = SPECS[item.source]
            task = asyncio.create_task(self.on_item(item, max_age_s=spec.max_age_s), name=f"rele-{item.id[:8]}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            log.info("aviso por el relé (%s, %.1f s de publicado): %s", item.source,
                     now - (item.published_at or now), item.title[:100])

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

    # -- ciclo -------------------------------------------------------------------------------------
    async def run(self, stop: asyncio.Event, session=None) -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            try:
                transport, _ = await loop.create_datagram_endpoint(lambda: self, remote_addr=(self.host, self.port))
                log.info("relé de Tokio: latidos a %s:%d", self.host, self.port)
                try:
                    while not stop.is_set():
                        transport.sendto(protocol.encode({"t": "hb"}, self.key, self.clock.time()))
                        self.counts["latidos"] += 1
                        if self.health.last_ok is not None and self.clock.time() - self.health.last_ok > 3 * self.heartbeat_s:
                            self.health.state = "degradada"
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_s)
                        except TimeoutError:
                            pass
                finally:
                    transport.close()
            except OSError as e:                                 # sin red: se reintenta (el relé nunca frena al agente)
                self.health.state = "degradada"
                log.warning("relé de Tokio: %s; reintento en 30 s", e)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30)
                except TimeoutError:
                    pass

    def snapshot(self) -> dict:
        return {"rtt_ms": self.rtt_ms, **dict(self.counts)}
