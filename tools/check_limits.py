"""Chequeo del CI (REGLAS A-1): config/limits.yaml respeta la línea base aprobada y la configuración por
defecto es solo paper. Sale con 1 si algo se relajó."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingagent import config, limits

errors = limits.problems(limits.load_raw()) + config.validate(config.load(None))
print("\n".join(errors) if errors else "Sobre de riesgo OK · modo paper")
sys.exit(1 if errors else 0)
