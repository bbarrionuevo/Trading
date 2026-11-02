"""Carpeta privada del proyecto TRADING, FUERA del repositorio: configuración, secretos, estado, diario y logs.

TRADING_HOME manda; si no está, Windows usa %LOCALAPPDATA%\\TRADING y el resto ~/.trading.
REGLAS G-5: este proyecto nunca usa ni toca las carpetas de nexus (otro proyecto del dueño que corre en la
misma PC). Si TRADING_HOME apunta a una carpeta de nexus, el agente se niega a arrancar.
"""
import json
import os
from pathlib import Path

SUBDIRS = ("state", "logs", "journal")
FOREIGN = "nexus"                      # nombre de carpeta del otro proyecto: nunca se usa


class ForeignHomeError(RuntimeError):
    pass


def _check_not_nexus(base: Path) -> None:
    resolved = base.expanduser().resolve()
    other = os.environ.get("NEXUS_HOME")
    if other and Path(other).expanduser().resolve() == resolved:
        raise ForeignHomeError("TRADING_HOME es la misma carpeta que NEXUS_HOME: TRADING nunca usa la de nexus")
    if any(part.lower() == FOREIGN for part in resolved.parts):
        raise ForeignHomeError(f"TRADING_HOME ({resolved}) está dentro de una carpeta de nexus: TRADING usa la suya "
                               "(%LOCALAPPDATA%\\TRADING por defecto)")


def home() -> Path:
    env = os.environ.get("TRADING_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TRADING"
    else:
        base = Path.home() / ".trading"
    _check_not_nexus(base)
    for sub in SUBDIRS:
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def state(name: str) -> Path:
    return home() / "state" / name


def kill_file() -> Path:
    """Interruptor de emergencia (REGLAS E-1): si existe, no se abren posiciones nuevas."""
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
