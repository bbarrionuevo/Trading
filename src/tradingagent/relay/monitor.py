"""La PC en modo "solo tablero" (el agente corre en Tokio, pedido del dueño del 5-nov): el supervisor late al relé y
guarda lo que manda Tokio para el tablero, sin el agente. Solo biblioteca estándar: el supervisor no carga
dependencias (así pip puede actualizarlas en Windows).

- El latido (cada 10 s, y apenas cambia) lleva si la PC está en pausa (`TRADING_HOME/KILL`): el relé la copia al
  agente de Tokio (relay/server.py), así el KILL de la PC sigue frenando todo.
- Lo que llega ("tel": el resumen del agente de Tokio, el estado del relé y las alertas) va a `state/tokio.json` y
  `state/tokio-eventos.jsonl` (relay/tablero.py), que lee el tablero.
"""
import logging
import socket
import threading
import time
from pathlib import Path

from .. import paths
from . import protocol
from .tablero import TokioSink

log = logging.getLogger("tradingagent.relay")
HEARTBEAT_S = 10.0
TICK_S = 1.0                                  # la pausa de la PC llega al relé en ~1 s
RETRY_S = 30.0


class TokioMonitor:
    def __init__(self, home: Path, host: str, port: int, key: bytes, *, heartbeat_s: float = HEARTBEAT_S,
                 tick_s: float = TICK_S):
        self.home, self.addr, self.key = Path(home), (host, int(port)), key
        self.heartbeat_s, self.tick_s = heartbeat_s, tick_s
        self.kill_file = self.home / "KILL"
        self.sink = TokioSink(self.home)
        self.seen = protocol.NonceCache()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.counts = {"latidos": 0, "acks": 0, "telemetria": 0, "rechazados": 0, "errores": 0}
        self.rtt_ms: float | None = None

    @classmethod
    def from_home(cls, home: Path, **kw) -> "TokioMonitor | None":
        """Con TRADING_HOME/relay.json (dirección) y relay.key; si falta algo, None."""
        conf = paths.read_json(Path(home) / "relay.json", None) or {}
        key_file = Path(home) / "relay.key"
        if not conf.get("host") or not key_file.exists():
            return None
        return cls(home, conf["host"], int(conf.get("port") or 50005), protocol.load_key(key_file), **kw)

    def start(self) -> "TokioMonitor":
        self.thread = threading.Thread(target=self.run, name="tokio-monitor", daemon=True)
        self.thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(self.tick_s)
                    sock.connect(self.addr)
                    log.info("solo tablero: latidos al relé de Tokio %s:%d", *self.addr)
                    self._loop(sock)
            except OSError as e:                        # sin red: se reintenta (nunca tumba al supervisor)
                self.counts["errores"] += 1
                log.warning("solo tablero: %s; reintento en %.0f s", e, RETRY_S)
                self.stop_event.wait(RETRY_S)

    def _loop(self, sock: socket.socket) -> None:
        last, last_kill = float("-inf"), None
        while not self.stop_event.is_set():
            now = time.time()
            kill = self.kill_file.exists()
            if now - last >= self.heartbeat_s or kill != last_kill:
                sock.send(protocol.encode({"t": "hb", "kill": kill}, self.key, now))
                self.counts["latidos"] += 1
                last, last_kill = now, kill
            try:
                data = sock.recv(2048)
            except TimeoutError:
                continue
            except ConnectionResetError:                # Windows: el aviso ICMP de un envío anterior; se sigue
                continue
            self.handle(data)

    def handle(self, data: bytes) -> None:
        now = time.time()
        msg = protocol.decode(data, self.key, self.seen, now)
        if msg is None:
            self.counts["rechazados"] += 1
            return
        if msg.get("t") == "ack":
            self.counts["acks"] += 1
            self.rtt_ms = round((now - float(msg.get("eco") or now)) * 1000, 1)
        elif msg.get("t") == "tel":
            self.counts["telemetria"] += 1
            try:
                self.sink.write(msg)
            except OSError as e:                        # Windows: el tablero lo tenía abierto; llega otro en 15 s
                log.debug("solo tablero: no pude guardar la telemetría: %s", e)

    def snapshot(self) -> dict:
        return {"rtt_ms": self.rtt_ms, **self.counts}
