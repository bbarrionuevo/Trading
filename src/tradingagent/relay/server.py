"""Relé de Tokio (Tier-0): corre en el servidor de AWS Lightsail en Tokio (ap-northeast-1) y reparte, por UDP firmado,
cada aviso oficial NUEVO que lee desde Asia. No tiene ninguna clave de trading ni de la IA: solo lee avisos públicos,
los reparte y pasa la telemetría del agente de Tokio al tablero de la PC.

Quién le habla (cada uno con su latido firmado, "hb"):
- la PC (desde Argentina, detrás del NAT): recibe los avisos y la telemetría del agente de Tokio. Su latido dice si
  la PC está en pausa (`kill`): el relé copia esa pausa al agente de Tokio (crea o borra SU archivo KILL, marcado),
  así /pausa por Telegram o el KILL de la PC frenan también lo que corre en Tokio.
- el agente de Tokio (127.0.0.1, el mismo servidor): recibe los mismos avisos (microsegundos) y manda su telemetría
  ("tel"), que el relé reenvía tal cual (con su firma) a la PC.

Fuentes (medido el 5-nov, docs/RELE-TOKIO.md): el canal OFICIAL de Upbit en Telegram (relay/telegram_upbit.py:
cada aviso sale 0 a 2 s después del sitio; el sitio de Upbit bloquea las IP de AWS) y la lista de anuncios de Binance
(una sola lectura por IP: el agente de Tokio no la lee por su cuenta, la recibe de acá). Desde el 7-nov esa lectura
trae, una vuelta sí y otra no, todos los catálogos: es el respaldo del WebSocket también para deslistados y vigilancia.

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
# Avisos oficiales que se sondean desde Tokio. Upbit NO: su sitio de avisos (Cloudflare) bloquea las IP de AWS (403,
# medido el 5-nov); no se esquiva: llega por el canal oficial de Telegram.
SOURCES = ("binance.listados",)
PEER_TTL_S = 45.0                                    # sin latido en 45 s, a ese no se le manda nada
PEER_FORGET_S = 600.0                                # y a los 10 min se olvida (la IP de la PC puede cambiar)
LOCAL_HOSTS = ("127.0.0.1", "::1")
KILL_MARK = "pausa copiada de la PC (relé)"          # el relé solo borra el KILL que creó él
STATUS_EVERY_S = 30.0


def is_local(addr) -> bool:
    return bool(addr) and addr[0] in LOCAL_HOSTS


class RelayServer(asyncio.DatagramProtocol):
    def __init__(self, key: bytes, clock=None, kill_file: Path | None = None):
        self.key, self.clock, self.kill_file = key, clock or SystemClock(), kill_file
        self.peers: dict[tuple, float] = {}           # dirección → último latido
        self.seen = protocol.NonceCache()
        self.transport = None
        self.counts: Counter = Counter()
        self.pc_kill: bool | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    # -- quién está ------------------------------------------------------------------------------------
    def live(self, now: float | None = None, *, local: bool | None = None) -> list[tuple]:
        now = self.clock.time() if now is None else now
        return [a for a, t in self.peers.items() if now - t <= PEER_TTL_S and (local is None or is_local(a) == local)]

    @property
    def peer(self) -> tuple | None:
        """La PC (la última que latió), para el registro."""
        remote = [(t, a) for a, t in self.peers.items() if not is_local(a)]
        return max(remote)[1] if remote else None

    def add_peer(self, addr, now: float) -> None:
        if addr not in self.peers:
            log.info("%s habla desde %s:%s", "el agente de Tokio" if is_local(addr) else "la PC", *addr[:2])
        self.peers[addr] = now
        for a, t in list(self.peers.items()):
            if now - t > PEER_FORGET_S:
                del self.peers[a]

    # -- lo que llega ----------------------------------------------------------------------------------
    def datagram_received(self, data: bytes, addr) -> None:
        now = self.clock.time()
        msg = protocol.decode(data, self.key, self.seen, now)
        if msg is None:
            self.counts["rechazados"] += 1                  # firma, hora o número repetido: se ignora en silencio
            return
        kind = msg.get("t")
        if kind == "hb":
            self.add_peer(addr, now)
            self.counts["latidos"] += 1
            self.transport.sendto(protocol.encode({"t": "ack", "eco": msg["ts"]}, self.key, now), addr)
            if not is_local(addr) and "kill" in msg:
                self.mirror_kill(bool(msg["kill"]))
        elif kind == "tel" and is_local(addr):              # telemetría del agente de Tokio → la PC, con su firma
            sent = 0
            for p in self.live(now, local=False):
                self.transport.sendto(data, p)
                sent += 1
            self.counts["telemetria" if sent else "telemetria_sin_pc"] += 1

    def mirror_kill(self, on: bool) -> None:
        """La pausa de la PC también frena al agente de Tokio. Solo se borra el KILL que puso el relé: uno creado en
        el servidor (a mano o por SSH) queda hasta que lo saque quien lo puso."""
        if on != self.pc_kill:
            log.warning("la PC %s", "está EN PAUSA (KILL)" if on else "no está en pausa")
            self.pc_kill = on
        f = self.kill_file
        if f is None:
            return
        try:
            if on and not f.exists():
                f.write_text(KILL_MARK + "\n", encoding="utf-8")
                log.warning("pausa copiada al agente de Tokio: %s", f)
            elif not on and f.exists() and f.read_text(encoding="utf-8").startswith(KILL_MARK):
                f.unlink()
                log.warning("la PC sacó la pausa: borré %s", f)
        except OSError as e:
            log.error("no pude copiar la pausa de la PC a %s: %s", f, e)

    # -- lo que se manda -------------------------------------------------------------------------------
    def forward(self, item) -> bool:
        """Manda el aviso a todos los que latieron hace poco (la PC y el agente de Tokio). El cuerpo se recorta hasta
        que entre en un datagrama."""
        now = self.clock.time()
        peers = self.live(now)
        if not peers or self.transport is None:
            self.counts["sin_nadie"] += 1
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
        for p in peers:
            self.transport.sendto(data, p)
        self.counts["avisos"] += 1
        return True

    def status(self, now: float | None = None) -> dict:
        from . import telegram_upbit
        now = self.clock.time() if now is None else now
        return {"t": "tel", "k": "rele", "pc": bool(self.live(now, local=False)),
                "agente": bool(self.live(now, local=True)), "pausa_pc": self.pc_kill,
                "telegram": telegram_upbit.STATUS.snapshot(now), **{k: v for k, v in self.counts.items()
                                                                       if k in ("avisos", "telegram", "telemetria",
                                                                                "rechazados", "sin_nadie")}}

    def send_status(self) -> None:
        """El estado del relé (y de Telegram) a la PC: lo muestra el tablero."""
        now = self.clock.time()
        if self.transport is None:
            return
        try:
            data = protocol.encode(self.status(now), self.key, now)
        except ValueError:
            return
        for p in self.live(now, local=False):
            self.transport.sendto(data, p)

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
            log.info("aviso %s (%s, %.1f s de publicado): %s", "mandado" if sent else "SIN NADIE", spec.id, age,
                     it.title[:100])

    async def on_telegram(self, item) -> None:
        """Un aviso de trading del canal oficial de Upbit en Telegram (relay/telegram_upbit.py)."""
        sent = self.forward(item)
        self.counts["telegram"] += 1
        log.info("aviso de Telegram %s (%.2f s de publicado): %s", "mandado" if sent else "SIN NADIE",
                 self.clock.time() - (item.published_at or item.received_at), item.title[:100])


async def _telegram(relay: RelayServer, stop: asyncio.Event) -> None:
    """El canal de Upbit en Telegram, si hay sesión iniciada; una falla no tira al relé."""
    from . import telegram_upbit
    if not telegram_upbit.ready():
        log.info("Telegram de Upbit apagado: falta el inicio de sesión (python -m tradingagent.relay.telegram_upbit "
                 "--login) o telethon")
        return
    try:
        await telegram_upbit.listen(relay.on_telegram, stop)
    except BaseException as e:                       # SystemExit incluido: el relé sigue con lo demás
        if isinstance(e, asyncio.CancelledError):
            raise
        log.error("Telegram de Upbit detenido: %s", e)


async def main(key_file: Path, port: int, kill_file: Path | None) -> None:
    key = protocol.load_key(key_file)
    loop = asyncio.get_running_loop()
    clock = SystemClock()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:                        # Windows (solo pruebas)
            pass
    transport, relay = await loop.create_datagram_endpoint(lambda: RelayServer(key, clock, kill_file),
                                                           local_addr=("0.0.0.0", port))
    log.info("relé escuchando UDP %d · la pausa de la PC se copia a %s", port, kill_file or "(ningún lado)")
    async with aiohttp.ClientSession() as session:
        specs = [s for s in DEFAULT_SOURCES if s.id in SOURCES]
        pollers = [PollingSource(s, session, clock, relay.on_items) for s in specs]
        tasks = [asyncio.create_task(p.run(stop), name=f"fuente-{p.spec.id}") for p in pollers]
        tasks.append(asyncio.create_task(_telegram(relay, stop), name="telegram-upbit"))
        last_log = time.time()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=STATUS_EVERY_S)
            except TimeoutError:
                relay.send_status()
                if time.time() - last_log >= 300:
                    last_log = time.time()
                    log.info("estado: PC %s · agente de Tokio %s · %s", relay.peer or "ninguna",
                             "sí" if relay.live(local=True) else "no", dict(relay.counts))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    transport.close()


def cli() -> None:
    ap = argparse.ArgumentParser(description="Relé de Tokio: avisos oficiales a la PC y al agente local, UDP firmado")
    ap.add_argument("--clave", default=str(Path.home() / "relay.key"))
    ap.add_argument("--puerto", type=int, default=50005)
    ap.add_argument("--kill", default=str(Path.home() / ".trading" / "KILL"),
                    help="el KILL del agente de Tokio, donde se copia la pausa de la PC ('' para no copiarla)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main(Path(args.clave), args.puerto, Path(args.kill) if args.kill else None))


if __name__ == "__main__":
    cli()
