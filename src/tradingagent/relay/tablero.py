"""Lo que manda Tokio, guardado en la PC para el tablero (dashboard.py lo lee; no hace E/S de red).

- `state/tokio.json`: lo último de cada parte ("agente": el resumen del agente de Tokio cada 15 s; "rele": el relé y
  el canal de Telegram cada 30 s), con la hora en que llegó a la PC.
- `state/tokio-eventos.jsonl`: cada alerta del agente de Tokio (entradas, salidas, frenos, errores): lo que en la PC
  iría a Telegram. Se recorta solo (las últimas ~2000).

Lo escriben el agente de la PC (relay/client.py, async) o, con la PC en "solo tablero", el supervisor
(relay/monitor.py, en un hilo). Solo biblioteca estándar.
"""
import asyncio
import json
import threading
import time
from pathlib import Path

from .. import paths

STATE_FILE = "tokio.json"
EVENTS_FILE = "tokio-eventos.jsonl"
MAX_EVENTS = 2000
SKIP = ("t", "n", "h")                                # campos del protocolo: no son datos


class TokioSink:
    def __init__(self, home: Path, clock=None):
        self.state_path = Path(home) / "state" / STATE_FILE
        self.events_path = Path(home) / "state" / EVENTS_FILE
        self.clock = clock
        self.latest: dict = paths.read_json(self.state_path, None) or {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return self.clock.time() if self.clock is not None else time.time()

    def write(self, msg: dict) -> None:
        part = str(msg.get("k") or "")
        data = {k: v for k, v in msg.items() if k not in SKIP and k != "k"}
        now = round(self._now(), 3)
        with self._lock:
            if part in ("agente", "rele"):
                self.latest[part] = {**data, "recibido": now}
                paths.write_json_atomic(self.state_path, self.latest)
            elif part == "alerta":
                self._append({**data, "recibido": now})

    async def __call__(self, msg: dict) -> None:
        await asyncio.to_thread(self.write, msg)

    def _append(self, row: dict) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.events_path.stat().st_size > MAX_EVENTS * 600:          # se recorta a la mitad, de vez en cuando
            lines = self.events_path.read_text(encoding="utf-8").splitlines()[-MAX_EVENTS // 2:]
            tmp = self.events_path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            try:
                tmp.replace(self.events_path)
            except OSError:                                             # Windows: el tablero lo tiene abierto
                tmp.unlink(missing_ok=True)
