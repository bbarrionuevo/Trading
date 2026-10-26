"""Rutas absolutas del sistema, FUERA del repositorio: estado, logs, interruptor y secretos.

NEXUS_HOME manda; si no está, Windows usa %LOCALAPPDATA%\\nexus y el resto ~/.nexus.
Así el interruptor de emergencia y los límites no dependen del directorio desde el que se lance.
"""
import json
import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("NEXUS_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "nexus"
    else:
        base = Path.home() / ".nexus"
    for sub in ("state", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def state(name: str) -> Path:
    return home() / "state" / name


def logs(name: str) -> Path:
    return home() / "logs" / name


def kill_file() -> Path:
    return home() / "KILL"


def write_json_atomic(path: Path, data) -> None:
    """Escribe a un temporal y reemplaza: un corte de luz nunca deja el archivo a medias."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return default


def load_env(path: Path = None) -> None:
    """Carga NEXUS_HOME/.env (KEY=valor) sin pisar variables ya definidas. Nunca imprime valores."""
    path = Path(path) if path else home() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
