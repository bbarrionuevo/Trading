"""Carpeta privada del agente, FUERA del repositorio: configuración, secretos, estado, diario y logs.

TRADING_HOME manda; si no está, Windows usa %LOCALAPPDATA%\\trading y el resto ~/.trading. Es una
carpeta distinta de la de nexus (%LOCALAPPDATA%\\nexus), que pertenece a otro proyecto.
"""
import json
import os
from pathlib import Path

SUBDIRS = ("state", "logs", "journal")


def home() -> Path:
    env = os.environ.get("TRADING_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "trading"
    else:
        base = Path.home() / ".trading"
    for sub in SUBDIRS:
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def state(name: str) -> Path:
    return home() / "state" / name


def kill_file() -> Path:
    """Interruptor de emergencia (REGLAS E-4): si existe, no se abren posiciones nuevas."""
    return home() / "KILL"


def write_json_atomic(path: Path, data) -> None:
    """Escribe a un temporal y lo reemplaza: un corte de luz nunca deja el archivo a medias."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def load_env(path: Path | None = None) -> list[str]:
    """Carga TRADING_HOME/.env (CLAVE=valor) sin pisar variables ya definidas. Nunca imprime valores.

    Devuelve los NOMBRES cargados (para diagnosticar qué falta sin revelar secretos)."""
    path = Path(path) if path else home() / ".env"
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k and k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")
                loaded.append(k)
    return loaded
