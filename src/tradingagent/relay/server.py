"""Relé de Tokio (Tier-0): corre en el servidor de AWS Lightsail en Tokio (ap-northeast-1) y le pasa a la PC, por UDP
firmado, cada aviso oficial NUEVO que lee desde Asia. No tiene ninguna clave de trading ni de la IA: solo lee avisos
públicos y avisa. La decisión y la orden siguen en la PC (un solo lugar con las reglas y el riesgo).

Por qué sirve (medido el 5-nov, docs/RELE-TOKIO.md): los avisos que se SONDEAN (Upbit, cada 8 s por su límite por IP;
la lista de anuncios de Binance) se leen también desde otra IP, cerca de Corea y de Binance, así que el aviso llega
antes en promedio (dos lectores desfasados). Los que Binance EMPUJA por WebSocket no se aceleran (cruzan el océano una
vez igual): para esos el relé es un segundo oído.

Uso en el servidor:  python -m tradingagent.relay.server --clave ~/relay.key --puerto 50005
"""
import argparse
import asyncio
import logging
import signal
import time
from collections import Counter
from pathlib import Path

import aiohttp

from ..core.clock import SystemClock
from ..ingestion.catalog import DEFAULT_SOURCES
from ..ingestion.sources import PollingSource
from . import protocol

log = logging.getLogger("tradingagent.relay")
SOURCES = ("upbit.avisos", "binance.listados")      # avisos oficiales que se sondean
PEER_TTL_S = 45.0                                    # sin latido de la PC en 45 s, no se manda nada


class RelayServer(asyncio.DatagramProtocol):
    def __init__(self, key: bytes, clock=None):
        self.key, self.clock = key, clock or SystemClock()
        self.peer: tuple | None = None
        self.peer_at = float("-inf")
        self.seen = protocol.NonceCache()
        self.transport = None
        self.counts: Counter = Counter()

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        msg = protocol.decode(data, self.key, self.seen, self.clock.time())
        if msg is None:
            self.counts["rechazados"] += 1                  # firma, hora o número repetido: se ignora en silencio
            return
        if msg.get("t") == "hb":
            if addr != self.peer:
                log.info("la PC habla desde %s:%s", *addr[:2])
            self.peer, self.peer_at = addr, self.clock.time()
            self.counts["latidos"] += 1
            self.transport.sendto(protocol.encode({"t": "ack", "eco": msg["ts"]}, self.key, self.clock.time()), addr)

    def forward(self, item) -> bool:
        """Manda el aviso a la PC si hay un latido reciente. El cuerpo se recorta hasta que entre en un datagrama."""
        now = self.clock.time()
        if self.peer is None or now - self.peer_at > PEER_TTL_S or self.transport is None:
            self.counts["sin_pc"] += 1
            return False
        body = item.body or ""
        for cut in (400, 200, 80, 0):
            payload = {"t": "sig", "src": item.source, "id": item.id, "title": item.title[:300], "body": body[:cut],
                       "pub": item.published_at, "url": (item.url or "")[:200], "rx": round(item.received_at, 3)}
            try:
                data = protocol.encode(payload, self.key, now)
                break
            except ValueError:
                continue
        else:
            self.counts["demasiado_largo"] += 1
            return False
        self.transport.sendto(data, self.peer)
        self.counts["avisos"] += 1
        return True

    async def on_items(self, spec, items, first: bool) -> None:
        """Lo que trae cada lectura: la primera es historia (no se manda); después, solo lo fresco."""
        if first:
            log.info("fuente %s: %d ítems de historia (no se mandan)", spec.id, len(items))
            return
        now = self.clock.time()
        for it in items:
            age = now - (it.published_at or it.received_at)
            if age > spec.max_age_s:
                continue
            sent = self.forward(it)
            log.info("aviso %s (%s, %.1f s de publicado): %s", "mandado" if sent else "SIN PC", spec.id, age,
                     it.title[:100])


async def main(key_file: Path, port: int) -> None:
    key = protocol.load_key(key_file)
    loop = asyncio.get_running_loop()
    clock = SystemClock()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:                        # Windows (solo pruebas)
            pass
    transport, relay = await loop.create_datagram_endpoint(lambda: RelayServer(key, clock), local_addr=("0.0.0.0", port))
    log.info("relé escuchando UDP %d", port)
    async with aiohttp.ClientSession() as session:
        specs = [s for s in DEFAULT_SOURCES if s.id in SOURCES]
        pollers = [PollingSource(s, session, clock, relay.on_items) for s in specs]
        tasks = [asyncio.create_task(p.run(stop), name=f"fuente-{p.spec.id}") for p in pollers]
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=300)
            except TimeoutError:
                peer = f"{relay.peer[0]}:{relay.peer[1]}" if relay.peer else "ninguna"
                log.info("estado: PC %s (último latido hace %.0f s) · %s", peer, time.time() - relay.peer_at,
                         dict(relay.counts))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    transport.close()


def cli() -> None:
    ap = argparse.ArgumentParser(description="Relé de Tokio: avisos oficiales a la PC por UDP firmado")
    ap.add_argument("--clave", default=str(Path.home() / "relay.key"))
    ap.add_argument("--puerto", type=int, default=50005)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main(Path(args.clave), args.puerto))


if __name__ == "__main__":
    cli()
