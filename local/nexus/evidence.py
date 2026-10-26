"""Evidencia para pasar a dinero real (R-15, criterios aprobados por el dueño el 24-oct-2026).

Lee el diario de vueltas cerradas en paper y calcula, para el total y por par:
  C1 duración ≥ 7 días · C2 ≥ 300 vueltas · C3 IC95% de la ganancia media > 0
  C4 positivo en ≥ 5 de los últimos 7 días · C5 sigue > 0 con +0,02% de comisión por lado
  C6 robustez a +200 ms de latencia (pendiente de medir: se informa como no cumplido)
Nivel de evidencia: paper (simulado con datos reales). La decisión de pasar a real es siempre del dueño.
"""
import json
import math
import time
from collections import defaultdict
from pathlib import Path

MIN_DAYS, MIN_TRADES, MIN_POS_DAYS, EXTRA_FEE_PCT = 7, 300, 5, 0.02
# Versión del modelo de llenado. 2 = ya no se llenan órdenes con operaciones previas a la orden
# (defecto 3, 24-oct-2026). 3 = la orden se llena recién cuando el volumen operado a su precio cubre
# la cola de adelante MÁS la orden entera (defecto 4, 25-oct-2026: antes bastaba con pasar la cola por
# una unidad). 4 = la cola de adelante nunca supera lo visible en nuestro nivel: si los demás cancelan,
# avanzamos (defecto 5, 25-oct-2026: el real se llenaba justo cuando el nivel se vaciaba antes de una
# baja y el paper no). 5 = una orden maker se llena SOLO si el precio la atraviesa (defecto 6, 26-oct-2026:
# con el modelo 4 el gemelo paper se llenaba de 2 a 30 veces más seguido que el real y ganaba más por
# vuelta en todos los pares: AGT +0,60% contra −0,23% real, RUNE +0,19% contra −0,07%, ZRO 0,00% contra
# −0,22%. Llenarse a nuestro precio depende de la cola, que desde la PC no se ve bien; se supone lo peor).
# Las vueltas de modelos anteriores quedan en el diario pero no cuentan.
FILL_MODEL = 5


def load(path, since=None, min_model=FILL_MODEL):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if (since is None or t.get("ts", 0) >= since) and t.get("model", 1) >= min_model:
            out.append(t)
    return out


def stats(pnls):
    n = len(pnls)
    if n == 0:
        return {"n": 0}
    mean = sum(pnls) / n
    var = sum((x - mean) ** 2 for x in pnls) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    gains, losses = sum(x for x in pnls if x > 0), -sum(x for x in pnls if x < 0)
    return {"n": n, "total": round(sum(pnls), 4), "mean": round(mean, 5), "sd": round(sd, 5),
            "ci95_low": round(mean - 1.96 * sd / math.sqrt(n), 5) if n > 1 else None,
            "profit_factor": round(gains / losses, 2) if losses > 0 else None,
            "win_rate": round(sum(x > 0 for x in pnls) / n, 3)}


def by_kind(trades):
    """Cuántas vueltas y cuánto dejó cada tipo de cierre (maker = venta con ganancia, stop, timeout)."""
    d = defaultdict(lambda: {"n": 0, "total": 0.0})
    for t in trades:
        k = d[t.get("kind", "?")]
        k["n"] += 1
        k["total"] = round(k["total"] + t["pnl"], 4)
    return dict(d)


def regime(t):
    """Clima de una vuelta al entrar, en categorías fijas y pocas (no se ajustan a los datos):
    volatilidad del par, tendencia del par (efficiency ratio) y BTC en los últimos 15 min."""
    v, er, ret, btc = t.get("vol_1m_pct"), t.get("er"), t.get("ret_window_pct"), t.get("btc_ret15_pct")
    vol = "sin dato" if v is None else "baja" if v <= 0.15 else "media" if v <= 0.3 else "alta" if v <= 0.6 else "muy alta"
    trend = "sin dato" if er is None else "lateral" if er < 0.5 else ("alcista" if (ret or 0) > 0 else "bajista")
    mkt = "sin dato" if btc is None else "BTC cae" if btc < -0.5 else "BTC sube" if btc > 0.5 else "BTC calmo"
    return {"volatilidad": vol, "tendencia": trend, "mercado": mkt}


def by_regime(trades):
    """Resultado por clima: {dimensión: {categoría: {n, total, mean}}}."""
    out = defaultdict(lambda: defaultdict(list))
    for t in trades:
        for dim, cat in regime(t).items():
            out[dim][cat].append(t["pnl"])
    return {d: {c: {"n": len(v), "total": round(sum(v), 4), "mean": round(sum(v) / len(v), 5)}
                for c, v in cats.items()} for d, cats in out.items()}


def by_variant(valid, now=None, hours=24):
    """Laboratorio por variante de estrategia en la MISMA ventana (últimas `hours` horas): la vigente,
    el control y las de lab.variants operan los mismos pares con los mismos datos."""
    now = now or time.time()
    d = defaultdict(list)
    for t in valid:
        if t.get("lab") and t["ts"] >= now - hours * 3600:
            d[t.get("strategy") or "sin versión"].append(t["pnl"])
    return {k: stats(v) for k, v in sorted(d.items())}


def by_selection(trades):
    """Laboratorio por estado de selección al ENTRAR (promovido / explorando, ver allocator.py).
    "promovido" es fuera de muestra: la promoción se decidió con vueltas anteriores."""
    d = defaultdict(list)
    for t in trades:
        if t.get("lab"):
            d[t.get("sel") or "sin dato"].append(t["pnl"])
    return {k: stats(v) for k, v in sorted(d.items())}


def daily(trades):
    d = defaultdict(float)
    for t in trades:
        d[time.strftime("%Y-%m-%d", time.gmtime(t["ts"]))] += t["pnl"]
    return dict(sorted(d.items()))


def criteria(trades, now=None):
    now = now or time.time()
    if not trades:
        return [("C1 ≥7 días", False, "sin vueltas todavía")]
    st = stats([t["pnl"] for t in trades])
    days = (now - min(t["ts"] for t in trades)) / 86400
    last7 = list(daily(trades).values())[-7:]
    fee_cost = sum(2 * t.get("notional", 40) * EXTRA_FEE_PCT / 100 for t in trades) / len(trades)
    return [
        (f"C1 ≥{MIN_DAYS} días", days >= MIN_DAYS, f"{days:.1f} días"),
        (f"C2 ≥{MIN_TRADES} vueltas", st["n"] >= MIN_TRADES, f"{st['n']} vueltas"),
        ("C3 IC95% > 0", (st.get("ci95_low") or -1) > 0, f"media {st['mean']:+.4f} € · IC95 desde {st.get('ci95_low')}"),
        (f"C4 positivo ≥{MIN_POS_DAYS}/7 días", sum(x > 0 for x in last7) >= MIN_POS_DAYS and len(last7) >= 7,
         f"{sum(x > 0 for x in last7)}/{len(last7)} días positivos"),
        (f"C5 +{EXTRA_FEE_PCT}% comisión", st["mean"] - fee_cost > 0, f"media ajustada {st['mean'] - fee_cost:+.4f} €"),
        ("C6 +200 ms latencia", False, "pendiente de medir"),
    ]


def evaluate(path, now=None, strategy=None):
    """Criterios sobre las vueltas del modelo de llenado vigente y, si se indica, de UNA versión de la
    estrategia. Las demás vueltas válidas se resumen aparte en `previous` (no cuentan). El grupo de
    control (misma estrategia sin filtro) se compara por clima en `regimes`."""
    valid = load(path)
    trades = [t for t in valid if strategy is None or t.get("strategy") == strategy]
    excluded = len(load(path, min_model=0)) - len(valid)
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t["sym"]].append(t)
    per_sym = {s: stats([t["pnl"] for t in ts]) | {"lab": any(t.get("lab") for t in ts), "by_kind": by_kind(ts)}
               for s, ts in by_sym.items()}
    crit = criteria(trades, now)
    return {"overall": stats([t["pnl"] for t in trades]), "by_kind": by_kind(trades), "daily": daily(trades),
            "per_symbol": per_sym,
            "criteria": [{"name": n, "ok": ok, "detail": d} for n, ok, d in crit],
            "criteria_met": sum(ok for _, ok, _ in crit), "criteria_total": len(crit),
            "excluded_old_model": excluded, "strategy": strategy,
            "previous": stats([t["pnl"] for t in valid if t not in trades]) if strategy else None,
            "selection": by_selection(trades),
            "variants": by_variant(valid, now),
            "regimes": {"vigente": by_regime(trades),
                        "control": by_regime([t for t in valid if t.get("strategy") == "control"])}}


if __name__ == "__main__":
    import sys
    print(json.dumps(evaluate(sys.argv[1]), indent=1, ensure_ascii=False))
