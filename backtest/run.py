"""Control de seguridad del CI (R-02, R-04, R-14, R-17).

Falla si: se activa el modo real, se relaja algún límite respecto de la línea base APROBADA POR EL
USUARIO (24-oct-2026) o la configuración no cumple el esquema. Bajar límites siempre está permitido.
Cambiar esta línea base es categoría C: requiere autorización humana explícita.
"""
import sys

import yaml

# Línea base aprobada. "max": el valor no puede superarlo. "min": no puede quedar por debajo.
BASELINE_MAX = {"max_position_eur": 40, "max_total_exposure_eur": 80, "daily_loss_limit_eur": 2,
                "weekly_loss_limit_eur": 4, "max_drawdown_eur": 8, "max_consecutive_stops": 3,
                "max_orders_per_minute": 30, "losing_days_window": 7}
BASELINE_MIN = {"stops_cooldown_min": 60}
AGENT_MAX = {"stop_pct": 1.0, "max_hold_min": 30, "stale_data_s": 20}
FIAT_MAX = {"safety_margin": 0.6}


def check(settings, agent):
    errs = []
    if settings.get("paper_trading") is not True:
        errs.append("paper_trading debe ser true (R-02)")
    if agent.get("live") is not False:
        errs.append("agent.live debe ser false (R-02)")
    lim = settings["limits"]
    if lim["safety_margin"] > FIAT_MAX["safety_margin"]:
        errs.append("limits.safety_margin relajado")
    if lim["eu"]["cumulative_eur_cap"] > 10000 or lim["ar"]["monthly_ars_cap"] > 50_000_000 \
            or lim["ar"]["per_tx_ars_cap"] > 2_500_000:
        errs.append("topes fiat por encima de los umbrales regulatorios")
    risk = agent.get("risk", {})
    for k, v in BASELINE_MAX.items():
        if k not in risk:
            errs.append(f"risk.{k} falta")
        elif not isinstance(risk[k], (int, float)) or risk[k] <= 0 or risk[k] > v:
            errs.append(f"risk.{k}={risk.get(k)} fuera de la línea base (máx {v})")
    for k, v in BASELINE_MIN.items():
        if not isinstance(risk.get(k), (int, float)) or risk[k] < v:
            errs.append(f"risk.{k}={risk.get(k)} por debajo de la línea base (mín {v})")
    for k, v in AGENT_MAX.items():
        if not isinstance(agent.get(k), (int, float)) or agent[k] <= 0 or agent[k] > v:
            errs.append(f"{k}={agent.get(k)} fuera de rango (máx {v})")
    if risk.get("max_total_exposure_eur", 0) > settings["capital_eur"]:
        errs.append("exposición total > capital")
    if risk.get("max_position_eur", 0) > risk.get("max_total_exposure_eur", 0):
        errs.append("posición > exposición total")
    if agent.get("maker_fee_pct", -1) < 0 or agent.get("taker_fee_pct", -1) < 0:
        errs.append("comisiones negativas")
    return errs


if __name__ == "__main__":
    s = yaml.safe_load(open("local/config/settings.yaml"))
    a = yaml.safe_load(open("local/config/agent.yaml"))
    e = check(s, a)
    print("\n".join(e) or "gate OK")
    sys.exit(1 if e else 0)
