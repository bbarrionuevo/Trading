"""Armado local del modo real (REGLAS A-2, F-4): sin esto, en la PC del dueño, no hay dinero real.

- `ARMADO.json` en TRADING_HOME lo crea SOLO el dueño, con `python -m tradingagent armar-real`, escribiendo una
  frase exacta. Guarda el capital máximo, los activos, el vencimiento y la huella de la clave (si cambia la
  clave, hay que volver a armar). El programa nunca lo crea solo.
- Calibración (F-4, antes de cumplir F-3): hasta 20 USDT y 7 días como máximo. Con F-3 cumplido (etapa 4):
  hasta `capital.max_live_usdt` del sobre y 30 días.
- `state/verificacion_real.json`: la prueba sin riesgo (`check.py`) tiene que haber pasado con esa misma
  clave hace menos de 30 días.
- Vencido, revocado (`armar-real --desarmar`) o con KILL: no se abre nada nuevo. Las posiciones abiertas
  salen normal: salir siempre se puede.
"""
import hashlib
import time
from pathlib import Path

from ... import paths

ARM_FILE = "ARMADO.json"
VERIFY_FILE = "verificacion_real.json"
VERIFY_MAX_AGE_S = 30 * 86400
CALIBRATION_MAX_USDT = 20.0                       # F-4
CALIBRATION_MAX_DAYS = 7                          # F-4
MAX_DAYS = 30                                     # A-2
VENUES = ("mexc-futuros",)
DEFAULT_ASSETS = ("BTC", "ETH", "SOL")


def fingerprint(access_key: str) -> str:
    """Huella corta de la clave (nunca la clave): sirve para saber si cambió."""
    return hashlib.sha256((access_key or "").encode()).hexdigest()[:12]


def phrase(capital: float, days: int) -> str:
    """La frase exacta que el dueño tiene que escribir para armar (A-2)."""
    return f"ARMO DINERO REAL {capital:g} USDT POR {days} DIAS"


def limits_for(limits: dict, calibration: bool) -> tuple[float, int]:
    """(capital máximo, días máximos) del armado."""
    if calibration:
        return min(CALIBRATION_MAX_USDT, float(limits["capital"]["max_live_usdt"])), CALIBRATION_MAX_DAYS
    return float(limits["capital"]["max_live_usdt"]), MAX_DAYS


def arm(home: Path, capital: float, days: int, access_key: str, limits: dict, typed: str, *,
        assets: tuple = DEFAULT_ASSETS, calibration: bool = True, now: float | None = None) -> dict:
    top, max_days = limits_for(limits, calibration)
    if not 0 < float(capital) <= top:
        raise ValueError(f"el capital tiene que ser mayor que 0 y como mucho {top:g} USDT")
    if not 1 <= int(days) <= max_days:
        raise ValueError(f"el armado vence en 1 a {max_days} días")
    if typed.strip() != phrase(capital, days):
        raise ValueError("la frase no coincide: no se armó nada")
    now = now if now is not None else time.time()
    data = {"capital_usdt": float(capital), "activos": sorted(a.upper() for a in assets), "venues": list(VENUES),
            "calibracion": bool(calibration), "clave": fingerprint(access_key), "armado_en": round(now),
            "vence": round(now + int(days) * 86400)}
    paths.write_json_atomic(Path(home) / ARM_FILE, data)
    return data


def disarm(home: Path) -> bool:
    p = Path(home) / ARM_FILE
    existed = p.exists()
    p.unlink(missing_ok=True)
    return existed


def save_verification(home: Path, report: dict, access_key: str, now: float | None = None) -> dict:
    data = {"ok": bool(report.get("ok")), "clave": fingerprint(access_key),
            "ts": round(now if now is not None else time.time()), "simbolo": report.get("simbolo"),
            "pasos": report.get("pasos", [])}
    paths.write_json_atomic(Path(home) / "state" / VERIFY_FILE, data)
    return data


def status(home: Path, access_key: str | None, limits: dict, now: float | None = None) -> tuple[bool, str, dict]:
    """(armado para ABRIR posiciones, motivo si no, datos del armado). Se consulta antes de cada entrada real."""
    home = Path(home)
    now = now if now is not None else time.time()
    data = paths.read_json(home / ARM_FILE, None)
    if not isinstance(data, dict):
        return False, "no_armado", {}
    if (home / "KILL").exists():
        return False, "kill", data
    if not access_key or data.get("clave") != fingerprint(access_key):
        return False, "clave_distinta", data
    top, max_days = limits_for(limits, bool(data.get("calibracion", True)))
    if not 0 < float(data.get("capital_usdt", 0)) <= top:
        return False, "capital_fuera_del_sobre", data
    if now >= float(data.get("vence", 0)) or float(data.get("vence", 0)) - float(data.get("armado_en", 0)) > \
            max_days * 86400 + 60:
        return False, "vencido", data
    ver = paths.read_json(home / "state" / VERIFY_FILE, None) or {}
    if not ver.get("ok") or ver.get("clave") != data["clave"]:
        return False, "sin_verificar", data
    if now - float(ver.get("ts", 0)) > VERIFY_MAX_AGE_S:
        return False, "verificacion_vieja", data
    return True, "", data


def asset_allowed(data: dict, symbol: str) -> bool:
    return symbol.split("_")[0].upper() in set(data.get("activos", []))
