"""El tablero completo de Tokio en la PC "solo tablero" (pedido del dueño, 5-nov: "en el dashboard no es necesaria esa
distinción de PC y Tokio"): el agente de Tokio escribe su tablero cada 30 s (`state/tablero-exportado.json`, con la
versión local y la pública: dashboard.export_board) y el supervisor de la PC lo trae por SSH, comprimido, cada 30 s
(`state/tablero-tokio.json`). El tablero de la PC lo muestra tal cual, como si el agente corriera acá.

Por SSH (cifrado y con la llave del dueño): la versión local puede llevar montos reales (fase 3); la pública nunca
(REGLAS G-8). Solo biblioteca estándar (el supervisor no carga dependencias). En Tokio no corre ningún proceso nuevo
(solo `gzip` sobre un archivo): el servidor tiene poca memoria.
"""
import gzip
import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .. import paths

log = logging.getLogger("tradingagent.relay")
EVERY_S = 30.0
REMOTE_FILE = "/home/ubuntu/.trading/state/tablero-exportado.json"
LOCAL_FILE = "tablero-tokio.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TokioBoardMirror:
    def __init__(self, home: Path, host: str, *, user: str = "ubuntu", key: Path | None = None,
                 every_s: float = EVERY_S, ssh: str | None = None, remote_file: str = REMOTE_FILE):
        self.home, self.host, self.user = Path(home), host, user
        self.key = Path(key) if key else Path.home() / ".ssh" / "lightsail-tokio.pem"
        self.every_s, self.remote_file = every_s, remote_file
        self.ssh = ssh or shutil.which("ssh")
        self.out = self.home / "state" / LOCAL_FILE
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.counts = {"ok": 0, "fallas": 0}
        self.last_error = ""

    @classmethod
    def from_home(cls, home: Path, **kw) -> "TokioBoardMirror | None":
        """Con la dirección de relay.json y la llave SSH ("ssh": {"usuario", "llave"} o ~/.ssh/lightsail-tokio.pem)."""
        conf = paths.read_json(Path(home) / "relay.json", None) or {}
        ssh_conf = conf.get("ssh") or {}
        key = Path(ssh_conf.get("llave") or Path.home() / ".ssh" / "lightsail-tokio.pem")
        if not conf.get("host") or not key.exists() or not shutil.which("ssh"):
            return None
        return cls(home, conf["host"], user=ssh_conf.get("usuario") or "ubuntu", key=key, **kw)

    def start(self) -> "TokioBoardMirror":
        self.thread = threading.Thread(target=self.run, name="tokio-espejo", daemon=True)
        self.thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def fetch_once(self) -> bool:
        cmd = [self.ssh, "-i", str(self.key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               f"{self.user}@{self.host}", f"gzip -c {self.remote_file}"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=40, creationflags=NO_WINDOW)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", errors="replace").strip()[:200] or f"código {r.returncode}")
            board = json.loads(gzip.decompress(r.stdout).decode("utf-8"))
            if not isinstance(board, dict) or "local" not in board or "publico" not in board:
                raise ValueError("el tablero de Tokio no tiene el formato esperado")
            paths.write_json_atomic(self.out, {**board, "recibido": round(time.time(), 3)})
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
            self.counts["fallas"] += 1
            self.last_error = str(e)[:200]
            log.debug("espejo de Tokio: %s", self.last_error)
            return False
        self.counts["ok"] += 1
        return True

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.fetch_once()
            self.stop_event.wait(self.every_s)
