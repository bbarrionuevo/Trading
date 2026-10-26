"""Detecta cambios en rutas protegidas (R-04, R-18). Uso: python backtest/protected_paths.py BASE HEAD

Sale con 1 si el cambio toca algo de categoría C. Ese PR requiere que lo apruebe y fusione una
persona: Claude nunca fusiona un PR con este control en rojo, y el actualizador del PC tampoco
activa versiones que cambien estas rutas sin la confirmación local del usuario.
"""
import subprocess
import sys

PROTECTED = (
    "backtest/",                       # el propio control de seguridad
    ".github/workflows/ci.yml",
    ".github/CODEOWNERS",
    "local/nexus/agent/risk.py",       # gestor de riesgo
    "local/nexus/agent/live.py",       # ejecución con dinero real (Fase 6)
    "local/nexus/agent/authorize.py",  # autorización local del dueño para operar en real
    "local/nexus/agent/hedge_live.py", # cobertura MEXC→Binance con dinero real
    "local/nexus/agent/controls.py",   # botones del tablero: autorizar, desbloquear, aprobar
    "local/nexus/dashboard.py",        # sirve esos botones (token, Host, Origin)
    "local/nexus/exchanges/",          # clientes de exchange (capacidad de operar)
    "local/nexus/paths.py",            # ubicación del interruptor y del estado
    "local/windows/update.py",         # actualizador seguro
    "docs/REGLAS.md",
)
# Protegidos en GitHub (requieren aprobación del dueño para fusionar) pero que NO cambian cómo opera
# el PC: el actualizador no pide aprobación local por ellos.
DOCS_ONLY = ("docs/REGLAS.md", ".github/CODEOWNERS")
# En estos archivos solo se protegen ciertas claves (el resto, p. ej. symbols, es categoría B).
PROTECTED_KEYS = {"local/config/agent.yaml": ("live", "risk", "stop_pct", "maker_fee_pct", "taker_fee_pct"),
                  "local/config/settings.yaml": ("paper_trading", "capital_eur", "limits")}


def changed(base, head):
    out = subprocess.run(["git", "diff", "--name-only", base, head], capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def show(rev, path):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def violations(base, head, runtime=False):
    """Cambios protegidos entre base y head. runtime=True: solo los que afectan la operación del PC."""
    import yaml
    v = [p for p in changed(base, head) if any(p == x or (x.endswith("/") and p.startswith(x)) for x in PROTECTED)]
    if runtime:
        v = [p for p in v if p not in DOCS_ONLY]
    for path, keys in PROTECTED_KEYS.items():
        a, b = yaml.safe_load(show(base, path) or "{}") or {}, yaml.safe_load(show(head, path) or "{}") or {}
        v += [f"{path}:{k}" for k in keys if a.get(k) != b.get(k)]
    return v


if __name__ == "__main__":
    found = violations(sys.argv[1], sys.argv[2])
    if found:
        print("Cambios de CATEGORÍA C (requieren aprobación humana explícita):")
        print("\n".join(f"  - {x}" for x in found))
        sys.exit(1)
    print("Sin cambios en rutas protegidas.")
