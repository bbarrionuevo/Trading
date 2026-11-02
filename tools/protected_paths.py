"""Rutas protegidas (REGLAS G-2): un PR que las toca queda en rojo y solo lo aprueba y fusiona el dueño.

  python tools/protected_paths.py BASE HEAD     sale con 1 si el cambio toca algo de categoría C
  python tools/protected_paths.py --self-test   verifica la propia lista
"""
import subprocess
import sys

PROTECTED = (
    ".github/",                            # CI y dueños de código
    "docs/REGLAS.md",                      # las reglas
    "config/limits.yaml",                  # el sobre de riesgo
    "src/tradingagent/limits.py",          # su línea base y su validación
    "src/tradingagent/safety.py",          # KILL, tope de órdenes por minuto y de precio
    "src/tradingagent/paths.py",           # dónde viven KILL y secretos, y la guarda contra las carpetas de nexus
    "src/tradingagent/execution/live/",    # ejecución con dinero real (todavía no existe)
    "tests/test_rules.py",                 # los tests que hacen cumplir las reglas
    "tools/",                              # este control y el chequeo del sobre
)


def protected(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in PROTECTED)


def changed(base: str, head: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", base, head], capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def self_test() -> int:
    cases = {"docs/REGLAS.md": True, "config/limits.yaml": True, "src/tradingagent/safety.py": True,
             "src/tradingagent/execution/live/binance.py": True, ".github/workflows/ci.yml": True,
             "tools/check_limits.py": True, "tests/test_rules.py": True,
             "src/tradingagent/execution/paper.py": False, "docs/PLAN.md": False, "tests/test_paper.py": False,
             "src/tradingagent/limits_helper.py": False}
    bad = [p for p, want in cases.items() if protected(p) != want]
    print("Autoprueba de rutas protegidas: " + ("OK" if not bad else f"FALLA en {bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    found = [p for p in changed(sys.argv[1], sys.argv[2]) if protected(p)]
    if found:
        print("Cambios de CATEGORÍA C (los aprueba y fusiona el dueño):")
        print("\n".join(f"  - {p}" for p in found))
        sys.exit(1)
    print("Sin cambios en rutas protegidas.")
