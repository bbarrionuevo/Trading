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
- Simulacro (`armar-real --simulacro`): el operador real completo sin plata en juego, sin frase ni prueba con
  órdenes. En Binance, por defecto LOCAL: precios y libro de producción, órdenes simuladas en la PC
  (binance_dryrun.py, modo "local"); con `--demo`, contra la DEMO de Binance (claves `BINANCE_API_KEY_TEST`, modo
  "demo"). En MEXC, contra un exchange simulado sobre el libro real (dryrun.py).
- Vencido, revocado (`armar-real --desarmar`) o con KILL: no se abre nada nuevo. Las posiciones abiertas
  salen normal: salir siempre se puede.
- **Por estrategia** (`armar-real --estrategias listado_upbit`, pedido del dueño del 6-nov: "el módulo de inversión
  automática de upbit ... ya quede funcionando con dinero real ... mientras seguimos haciendo pruebas ... con los demás
  módulos"): el armado guarda la lista y solo esas estrategias abren con dinero real; las demás siguen en la práctica.
  Sin lista, todas (lo de antes). Lo controla el operador (`live.py`) y, de nuevo, el venue antes de cada orden.
- **Simulacro junto al dinero real** (el dueño, 6-nov: "opción 1 siempre que los simulacros de los demás módulos no
  compitan o afecten en nada a los módulos que trabajen con dinero real"): con el dinero real armado, `armar-real
  --simulacro` arma un simulacro aparte (`SIMULACRO.json`, siempre local y de mentira: ese archivo nunca arma plata).
  Corre en el mismo agente y nunca opera las estrategias del dinero real (`live.py`).
- Un solo ejecutor por cuenta (REGLAS G-9): si el agente del servidor de Tokio informó en la última hora (su
  telemetría, `state/tokio.json`) que opera DINERO REAL, esta máquina no abre con dinero real. En Tokio ese archivo
  no existe: allá no frena nada.
"""
import hashlib
import time
from pathlib import Path

from ... import paths

ARM_FILE = "ARMADO.json"
SIM_FILE = "SIMULACRO.json"                       # el simulacro armado junto al dinero real: nunca arma plata
VERIFY_FILE = "verificacion_real.json"                   # MEXC
VERIFY_FILES = {"mexc-futuros": VERIFY_FILE, "binance-futuros": "verificacion_binance.json"}
VERIFY_MAX_AGE_S = 30 * 86400
MAX_DAYS = 30                                     # A-2
ALL = "todo_el_saldo"                             # capital del armado real: todo el saldo de la cuenta
VENUES = ("binance-futuros", "mexc-futuros")
DEFAULT_VENUE = "binance-futuros"
ALL_ASSETS = "TODOS"                              # cualquier activo del universo operable (lo que mueva la noticia)
OTHER_EXECUTOR_S = 3600                           # G-9: Tokio dijo "dinero real" hace menos de una hora
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
        venue: str = "mexc-futuros", mode: str | None = None, strategies: tuple | None = None,
        file: str = ARM_FILE) -> dict:
    """Arma el dinero real (todo el saldo de la cuenta) o, con `simulacro=True`, el simulacro con `capital` USDT
    de mentira: el operador real corre completo pero NUNCA manda órdenes (no pide frase ni prueba con órdenes).
    `strategies`: solo esas estrategias abren (None: todas). `file=SIM_FILE`: el simulacro junto al dinero real."""
    if file not in (ARM_FILE, SIM_FILE):
        raise ValueError(f"archivo de armado desconocido: {file}")
    if file == SIM_FILE and not (simulacro and venue == "binance-futuros" and mode == "local"):
        raise ValueError("junto al dinero real solo va el simulacro local de Binance (plata de mentira)")
    if not 1 <= int(days) <= MAX_DAYS:
        raise ValueError(f"el armado vence en 1 a {MAX_DAYS} días")
    if venue not in VENUES:
        raise ValueError(f"exchange desconocido: {venue}")
    if mode not in (None, "local", "demo") or (mode and not simulacro):
        raise ValueError("el modo es solo del simulacro: local o demo")
    only = None if strategies is None else sorted({str(s).strip() for s in strategies if str(s).strip()})
    if only is not None and not only:
        raise ValueError("la lista de estrategias está vacía: no se armó nada")
    if simulacro:
        if capital is None or not float(capital) > 0:
            raise ValueError("el simulacro necesita un saldo de mentira mayor que 0 (--capital)")
    elif typed.strip() != phrase(days):
        raise ValueError("la frase no coincide: no se armó nada")
    now = now if now is not None else time.time()
    data = {"capital_usdt": float(capital) if simulacro else ALL, "activos": sorted(a.upper() for a in assets),
            "venues": [venue], "simulacro": bool(simulacro), "clave": fingerprint(access_key),
            "armado_en": round(now), "vence": round(now + int(days) * 86400), **({"modo": mode} if mode else {}),
            **({"estrategias": only} if only is not None else {})}
    paths.write_json_atomic(Path(home) / file, data)
    return data


def disarm(home: Path, file: str = ARM_FILE) -> bool:
    if file not in (ARM_FILE, SIM_FILE):
        raise ValueError(f"archivo de armado desconocido: {file}")
    p = Path(home) / file
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
           venue: str | None = None, file: str = ARM_FILE) -> tuple[bool, str, dict]:
    """(armado para ABRIR posiciones, motivo si no, datos del armado). Se consulta antes de cada entrada real. Con
    `venue`, el armado tiene que ser de ese exchange (un armado de MEXC no arma Binance). `file=SIM_FILE`: el
    simulacro junto al dinero real (si ese archivo dijera dinero real, no arma nada)."""
    home = Path(home)
    now = now if now is not None else time.time()
    if file not in (ARM_FILE, SIM_FILE):
        return False, "archivo_desconocido", {}
    data = paths.read_json(home / file, None)
    if not isinstance(data, dict):
        return False, "no_armado", {}
    if file == SIM_FILE and not data.get("simulacro"):
        return False, "no_es_simulacro", data
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
    if not sim and other_executor(home, now):          # G-9: el dinero real ya lo opera el servidor de Tokio
        return False, "otro_ejecutor", data
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


def other_executor(home: Path, now: float) -> bool:
    """REGLAS G-9: ¿el agente de Tokio opera dinero real (según su telemetría de la última hora)?"""
    data = paths.read_json(Path(home) / "state" / "tokio.json", None)
    ag = data.get("agente") if isinstance(data, dict) else None
    if not isinstance(ag, dict):
        return False
    try:
        fresh = now - float(ag.get("recibido") or 0) <= OTHER_EXECUTOR_S
    except (TypeError, ValueError):
        return False
    return fresh and any(isinstance(a, dict) and a.get("tipo") == "real" for a in ag.get("cuentas") or [])


def strategy_allowed(data: dict, category: str | None) -> bool:
    """¿El armado deja ABRIR esta estrategia (la categoría de la entrada)? Sin lista, todas. Con lista, solo esas, y
    una entrada que no dice su estrategia no pasa."""
    only = (data or {}).get("estrategias")
    if only is None:
        return True
    return category is not None and str(category) in {str(s) for s in only}


def asset_allowed(data: dict, symbol: str, key: str | None = None) -> bool:
    """¿El armado permite este contrato? "TODOS" (lo normal): cualquiera del universo operable."""
    allowed = {str(a).upper() for a in data.get("activos", [])}
    base = symbol.split("_")[0].upper()
    if base.endswith("USDT") and len(base) > 4:          # Binance: BTCUSDT → BTC
        base = base[:-4]
    return ALL_ASSETS in allowed or base in allowed or (key or "").upper() in allowed
