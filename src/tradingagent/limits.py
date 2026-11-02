"""Sobre de riesgo (REGLAS A-1): los límites duros, fuera del alcance de estrategias, NLP y Claude.

config/limits.yaml lo fija el dueño. BASELINE es la línea base aprobada: el CI y el agente rechazan un
archivo más laxo; más estricto siempre se permite. Este módulo y limits.yaml son rutas protegidas
(categoría C, REGLAS G-2): solo el dueño aprueba y fusiona sus cambios. En ejecución el agente puede
achicar el sobre (por ejemplo tras pérdidas), nunca agrandarlo.
"""
from pathlib import Path

import yaml

REPO_FILE = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"

# (sección, clave): ("max", tope) = el valor no puede superarlo · ("min", piso) = no puede quedar debajo ·
# ("eq", valor) = tiene que ser exactamente ése.
BASELINE = {
    ("capital", "max_live_usdt"): ("max", 120),
    ("per_trade", "max_risk_pct"): ("max", 1.0),
    ("per_trade", "max_position_pct"): ("max", 40),
    ("per_trade", "max_leverage"): ("max", 1),
    ("per_trade", "max_slippage_bps"): ("max", 50),
    ("per_trade", "require_stop"): ("eq", True),
    ("portfolio", "max_gross_exposure_pct"): ("max", 80),
    ("portfolio", "max_positions"): ("max", 3),
    ("loss", "daily_pct"): ("max", 2.0),
    ("loss", "weekly_pct"): ("max", 4.0),
    ("loss", "max_drawdown_pct"): ("max", 8.0),
    ("loss", "max_consecutive_losses"): ("max", 3),
    ("loss", "cooldown_min"): ("min", 60),
    ("market", "max_spread_bps"): ("max", 30),
    ("market", "min_depth_multiple"): ("min", 5),
    ("market", "max_book_age_s"): ("max", 5),
    ("news", "max_age_s"): ("max", 600),
    ("news", "min_sources_unverified"): ("min", 2),
    ("execution", "max_orders_per_min"): ("max", 30),
    ("ai", "max_llm_usd_per_day"): ("max", 5),
}


def problems(lim: dict) -> list[str]:
    errs = []
    for (sec, key), (kind, base) in BASELINE.items():
        name = f"{sec}.{key}"
        try:
            v = lim[sec][key]
        except (KeyError, TypeError):
            errs.append(f"{name}: falta")
            continue
        if kind == "eq":
            if v is not base and v != base:
                errs.append(f"{name}={v!r}: tiene que ser {base!r}")
            continue
        if isinstance(v, bool) or not isinstance(v, int | float) or v <= 0:
            errs.append(f"{name}={v!r}: tiene que ser un número positivo")
        elif kind == "max" and v > base:
            errs.append(f"{name}={v} más laxo que la línea base aprobada (máximo {base})")
        elif kind == "min" and v < base:
            errs.append(f"{name}={v} más laxo que la línea base aprobada (mínimo {base})")
    if not errs and lim["per_trade"]["max_position_pct"] > lim["portfolio"]["max_gross_exposure_pct"]:
        errs.append("per_trade.max_position_pct no puede superar portfolio.max_gross_exposure_pct")
    if not errs and lim["loss"]["daily_pct"] > lim["loss"]["weekly_pct"]:
        errs.append("loss.daily_pct no puede superar loss.weekly_pct")
    return errs


def load_raw(path: Path | None = None) -> dict:
    """El sobre tal como está escrito (sin validar). Sin archivo, la línea base tal cual."""
    path = Path(path) if path else REPO_FILE
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    lim: dict = {}
    for (sec, key), (_, base) in BASELINE.items():
        lim.setdefault(sec, {})[key] = base
    return lim


def load(path: Path | None = None) -> dict:
    """Lee y valida el sobre; si es inválido o más laxo que la línea base, el agente no arranca."""
    lim = load_raw(path)
    errs = problems(lim)
    if errs:
        raise ValueError("sobre de riesgo inválido (config/limits.yaml):\n  - " + "\n  - ".join(errs))
    return lim
