"""Acciones del dueño desde el tablero local (botones), para no tener que tipear comandos.

Cada acción hace EXACTAMENTE lo mismo que su comando de consola y pide las mismas confirmaciones:
autorizar dinero real o la cobertura exige confirmar la clave (sin retiro) y escribir la frase exacta;
aprobar una actualización exige escribir "APRUEBO <versión>". Lo que cambia es solo dónde se escribe.
El tablero escucha únicamente en 127.0.0.1 y cada pedido lleva un token de sesión y se valida el
origen (ver dashboard.py): una página de internet abierta en el navegador no puede apretar botones.
Claude nunca usa estas acciones: son del dueño, en su PC.
"""
import contextlib
import io
import subprocess
import sys
from pathlib import Path

from .. import paths

REPO = Path(__file__).resolve().parents[3]

KILL_LEVELS = {1: "no abre posiciones nuevas", 2: "cancela todas las órdenes", 3: "cancela todo y vende a mercado"}


def _answers(*xs):
    it = iter(xs)
    return lambda prompt: next(it, "")


def _run(fn, *args, **kw):
    """Corre una función de consola juntando lo que imprime."""
    lines, err = [], io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            code = fn(*args, out=lines.append, **kw)
    except SystemExit:                                               # argparse: parámetro inválido
        return {"ok": False, "out": lines + (err.getvalue().strip().splitlines()[-1:] or ["Parámetros inválidos."])}
    except Exception as e:                                           # noqa: BLE001 (se informa)
        return {"ok": False, "out": lines + [f"Error: {type(e).__name__}: {str(e)[:200]}"]}
    return {"ok": code == 0, "out": lines}


def _pairs(text):
    return ",".join(s.strip().upper() for s in str(text or "").split(",") if s.strip())


def kill(level):
    level = int(level)
    if level not in KILL_LEVELS:
        return {"ok": False, "out": ["Nivel de KILL inválido (1, 2 o 3)."]}
    paths.kill_file().write_text(f"{level} dueño desde el tablero", encoding="utf-8")
    return {"ok": True, "out": [f"KILL {level} puesto: {KILL_LEVELS[level]}."]}


def resume():
    k = paths.kill_file()
    if not k.exists():
        return {"ok": True, "out": ["No había ninguna pausa."]}
    k.unlink()
    return {"ok": True, "out": ["Pausa quitada: el agente vuelve a operar normal (los límites de riesgo siguen)."]}


def authorize_real(p):
    from . import authorize
    argv = ["--dias", str(p.get("dias") or 7)]
    if p.get("todo", True):
        argv.append("--todo")
    else:
        argv += ["--capital", str(p.get("capital") or 20)]
    if p.get("auto"):
        argv += ["--auto", str(int(p["auto"]))]
    else:
        argv += ["--pares", _pairs(p.get("pares"))]
    if p.get("calibracion", True):
        argv.append("--calibracion")
    return _run(authorize.main, argv, ask=_answers("SI" if p.get("clave_sin_retiro") else "no", p.get("frase", "")))


def authorize_hedge(p):
    from . import authorize
    argv = ["--cobertura", _pairs(p.get("pares")), "--lote", str(p.get("lote") or 10), "--dias", str(p.get("dias") or 7),
            "--calibracion"]
    return _run(authorize.main, argv, ask=_answers("SI" if p.get("clave_sin_retiro") else "no", p.get("frase", "")))


def revoke():
    from . import authorize
    return _run(authorize.main, ["--revocar"])


def status():
    from . import authorize
    return _run(authorize.main, ["--estado"])


def unblock(p):
    from . import authorize
    if not _pairs(p.get("pares")):
        return {"ok": False, "out": ["Indicá qué pares desbloquear (p. ej. BDXUSDT)."]}
    flag = "--desbloquear-cobertura" if p.get("cobertura") else "--desbloquear"
    return _run(authorize.main, [flag, _pairs(p.get("pares"))], ask=_answers("SI" if p.get("revisado") else "no"))


def _update_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("update", REPO / "local" / "windows" / "update.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def update_candidate():
    try:
        cur, new, prot, history = _update_module().candidate()
    except Exception as e:                                           # noqa: BLE001
        return {"ok": False, "out": [f"No se pudo consultar GitHub: {type(e).__name__}"]}
    if cur == new:
        return {"ok": True, "out": [f"Al día: versión {cur[:7]}."], "candidate": None}
    return {"ok": True, "candidate": new[:7], "protected": prot,
            "out": [f"Versión nueva {new[:7]} (instalada {cur[:7]}). Cambios protegidos: {', '.join(prot) or 'ninguno'}.",
                    *history.splitlines()[:15]]}


def approve_update(p):
    return _run(_update_module().approve, ask=_answers(p.get("frase", "")))


def update_now():
    """Corre ya el mantenimiento (actualización segura incluida) en vez de esperar los 10 minutos."""
    if sys.platform != "win32":
        return {"ok": False, "out": ["Solo en la PC con Windows (tarea nexus-mantenimiento)."]}
    r = subprocess.run(["schtasks", "/Run", "/TN", "nexus-mantenimiento"], capture_output=True, text=True, timeout=30)
    return {"ok": r.returncode == 0, "out": [(r.stdout or r.stderr).strip() or "Mantenimiento lanzado."]}


def check_keys():
    from .. import check_keys as ck
    paths.load_env()                                                 # las claves no se muestran: solo su estado
    return _run(ck.run, check_clock=True)


def preflight():
    from .. import preflight as pf
    paths.load_env()
    try:
        return {"ok": True, "out": pf.describe(pf.run()).splitlines()}
    except Exception as e:                                           # noqa: BLE001
        return {"ok": False, "out": [f"Error: {type(e).__name__}: {str(e)[:200]}"]}


ACTIONS = {
    "kill": lambda p: kill(p.get("nivel", 1)), "reanudar": lambda p: resume(),
    "autorizar": authorize_real, "cobertura": authorize_hedge, "revocar": lambda p: revoke(),
    "estado": lambda p: status(), "desbloquear": unblock,
    "actualizacion": lambda p: update_candidate(), "aprobar": approve_update, "actualizar_ya": lambda p: update_now(),
    "claves": lambda p: check_keys(), "chequeo": lambda p: preflight(),
}


def run(action, params=None):
    fn = ACTIONS.get(action)
    if not fn:
        return {"ok": False, "out": [f"Acción desconocida: {action}"]}
    return fn(params or {})
