"""Armado local del modo real (REGLAS A-2): sin esto, en la PC del dueño, no hay dinero real.

- `ARMADO.json` en TRADING_HOME lo crea SOLO el dueño, con `python -m tradingagent armar-real`, escribiendo una
  frase exacta. Guarda los activos, el vencimiento y la huella de la clave (si cambia la clave, hay que volver
  a armar). El programa nunca lo crea solo.
- **Sin tope de capital** (el dueño, 3-nov-2026: "elimina los topes, siempre se va a trabajar e invertir con
  todo el dinero disponible"): el operador real trabaja con TODO el saldo de la cuenta de futuros de MEXC y
  crece con él. El riesgo lo acotan los porcentajes del sobre (riesgo por operación, exposición, frenos del día,
  la semana y el drawdown), que se escalan con el saldo.
- Vence a los 30 días como máximo (A-2): se vuelve a armar con el mismo comando.
- La prueba sin riesgo con órdenes tiene que haber pasado con esa misma clave hace menos de 30 días:
  `state/verificacion_binance.json` (`binance-real-check`, Binance, desde el 4-nov-2026) o
  `state/verificacion_real.json` (`mexc-real-check`, MEXC).
- Simulacro (`armar-real --simulacro`): el operador real completo con un saldo de mentira y sin plata en juego, sin
  frase ni prueba con órdenes. En Binance va contra la DEMO (plata ficticia, claves `BINANCE_API_KEY_TEST`); en
  MEXC, contra un exchange simulado sobre el libro real (dryrun.py).
- Vencido, revocado (`armar-real --desarmar`) o con KILL: no se abre nada nuevo. Las posiciones abiertas
  salen normal: salir siempre se puede.
"""
import hashlib
import time
from pathlib import Path

from ... import paths

ARM_FILE = "ARMADO.json"
VERIFY_FILE = "verificacion_real.json"                   # MEXC
VERIFY_FILES = {"mexc-futuros": VERIFY_FILE, "binance-futuros": "verificacion_binance.json"}
VERIFY_MAX_AGE_S = 30 * 86400
MAX_DAYS = 30                                     # A-2
ALL = "todo_el_saldo"                             # capital del armado real: todo el saldo de la cuenta
VENUES = ("binance-futuros", "mexc-futuros")
DEFAULT_VENUE = "binance-futuros"
ALL_ASSETS = "TODOS"                              # cualquier activo del universo operable (lo que mueva la noticia)
DEFAULT_ASSETS = (ALL_ASSETS,)


def fingerprint(access_key: str) -> str:
    """Huella corta de la clave (nunca la clave): sirve para saber si cambió."""
    return hashlib.sha256((access_key or "").encode()).hexdigest()[:12]


def phrase(days: int) -> str:
    """La frase exacta que el dueño tiene que escribir para armar (A-2)."""
    return f"ARMO DINERO REAL CON TODO EL SALDO POR {days} DIAS"


def venue_of(data: dict) -> str:
    """El exchange de un armado (los de antes del 4-nov-2026 eran de MEXC)."""
    venues = (data or {}).get("venues") or ["mexc-futuros"]
    return str(venues[0])


def arm(home: Path, days: int, access_key: str, typed: str, *, assets: tuple = DEFAULT_ASSETS,
        simulacro: bool = False, capital: float | None = None, now: float | None = None,
        venue: str = "mexc-futuros") -> dict:
    """Arma el dinero real (todo el saldo de la cuenta) o, con `simulacro=True`, el simulacro con `capital` USDT
    de mentira: el operador real corre completo pero NUNCA manda órdenes (no pide frase ni prueba con órdenes)."""
    if not 1 <= int(days) <= MAX_DAYS:
        raise ValueError(f"el armado vence en 1 a {MAX_DAYS} días")
    if venue not in VENUES:
        raise ValueError(f"exchange desconocido: {venue}")
    if simulacro:
        if capital is None or not float(capital) > 0:
            raise ValueError("el simulacro necesita un saldo de mentira mayor que 0 (--capital)")
    elif typed.strip() != phrase(days):
        raise ValueError("la frase no coincide: no se armó nada")
    now = now if now is not None else time.time()
    data = {"capital_usdt": float(capital) if simulacro else ALL, "activos": sorted(a.upper() for a in assets),
            "venues": [venue], "simulacro": bool(simulacro), "clave": fingerprint(access_key),
            "armado_en": round(now), "vence": round(now + int(days) * 86400)}
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


def status(home: Path, access_key: str | None, now: float | None = None,
           venue: str | None = None) -> tuple[bool, str, dict]:
    """(armado para ABRIR posiciones, motivo si no, datos del armado). Se consulta antes de cada entrada real. Con
    `venue`, el armado tiene que ser de ese exchange (un armado de MEXC no arma Binance)."""
    home = Path(home)
    now = now if now is not None else time.time()
    data = paths.read_json(home / ARM_FILE, None)
    if not isinstance(data, dict):
        return False, "no_armado", {}
    if venue is not None and venue_of(data) != venue:
        return False, "otro_exchange", data
    if (home / "KILL").exists():
        return False, "kill", data
    sim = bool(data.get("simulacro"))
    if not sim and (not access_key or data.get("clave") != fingerprint(access_key)):
        return False, "clave_distinta", data
    capital = data.get("capital_usdt")
    if sim and not (isinstance(capital, int | float) and capital > 0):
        return False, "simulacro_sin_saldo", data
    if not sim and capital != ALL:                    # un armado viejo, con tope: hay que volver a armar
        return False, "volver_a_armar", data
    if now >= float(data.get("vence", 0)) or float(data.get("vence", 0)) - float(data.get("armado_en", 0)) > \
            MAX_DAYS * 86400 + 60:
        return False, "vencido", data
    if sim:
        return True, "", data                         # sin órdenes reales: no hace falta la prueba con órdenes
    ver = paths.read_json(home / "state" / VERIFY_FILES.get(venue_of(data), VERIFY_FILE), None) or {}
    if not ver.get("ok") or ver.get("clave") != data["clave"]:
        return False, "sin_verificar", data
    if now - float(ver.get("ts", 0)) > VERIFY_MAX_AGE_S:
        return False, "verificacion_vieja", data
    return True, "", data


def asset_allowed(data: dict, symbol: str, key: str | None = None) -> bool:
    """¿El armado permite este contrato? "TODOS" (lo normal): cualquiera del universo operable."""
    allowed = {str(a).upper() for a in data.get("activos", [])}
    base = symbol.split("_")[0].upper()
    if base.endswith("USDT") and len(base) > 4:          # Binance: BTCUSDT → BTC
        base = base[:-4]
    return ALL_ASSETS in allowed or base in allowed or (key or "").upper() in allowed
