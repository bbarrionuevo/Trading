"""Carry de funding con datos REALES pasados: ¿cuánto habría cobrado mantener la cobertura?

Carry = comprar contado y vender el perpetuo por el mismo monto. El precio se cancela entre las dos
patas y queda el funding que pagan los largos cuando la tasa es positiva. En vez de esperar días de
paper, se usa el historial público de tasas ya liquidadas: si un par pagó de forma sostenida las
últimas semanas, es candidato; si la tasa se da vuelta seguido, no.
No incluye el riesgo de base (perpetuo vs contado al entrar y salir) ni el de liquidación de la
pata corta: por eso el informe muestra también la peor racha de 24 h.
"""
import statistics
import time

import requests

MEXC_C = "https://contract.mexc.com/api/v1/contract"
GATE_F = "https://api.gateio.ws/api/v4/futures/usdt"
S = requests.Session()


def _get(url, **kw):
    r = S.get(url, timeout=(5, 15), **kw)
    r.raise_for_status()
    return r.json()


def mexc_history(symbol, pages=3, page_size=100):
    """[(ts_segundos, tasa)] liquidadas, de la más vieja a la más nueva."""
    out = []
    for page in range(1, pages + 1):
        d = _get(f"{MEXC_C}/funding_rate/history",
                 params={"symbol": symbol, "page_num": page, "page_size": page_size}).get("data") or {}
        rows = d.get("resultList") or []
        out += [(int(r["settleTime"]) / 1000, float(r["fundingRate"])) for r in rows]
        if page >= int(d.get("totalPage") or 1):
            break
        time.sleep(0.2)
    return sorted(set(out))


def mexc_cycle_hours(symbol):
    """Intervalo de cobro declarado por MEXC (horas) o None."""
    d = _get(f"{MEXC_C}/funding_rate/{symbol}").get("data") or {}
    return d.get("collectCycle")


def gate_history(contract, limit=300):
    return sorted((int(r["t"]), float(r["r"])) for r in _get(f"{GATE_F}/funding_rate",
                                                              params={"contract": contract, "limit": limit}))


def carry_stats(history, roundtrip_cost_pct=0.2, now=None, windows=(7, 30)):
    """Resumen del carry largo contado / corto perpetuo sobre el historial [(ts, tasa)].

    Por ventana: tasa sumada (%), APR equivalente, fracción de cobros positivos y neto tras pagar
    una vez la entrada+salida (roundtrip_cost_pct). También la peor suma de 24 h y la mediana del
    intervalo entre cobros (para no asumir 8 h).
    """
    if len(history) < 2:
        return None
    now = now or history[-1][0]
    ts = [t for t, _ in history]
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    interval_h = statistics.median(gaps) / 3600 if gaps else None
    out = {"periods": len(history), "interval_h": round(interval_h, 2) if interval_h else None,
           "days_covered": round((ts[-1] - ts[0]) / 86400, 1), "last_rate_pct": round(history[-1][1] * 100, 4)}
    for w in windows:
        rows = [r for t, r in history if t > now - w * 86400]
        if not rows:
            out[f"d{w}"] = None
            continue
        span = min(w, max((now - min(t for t, _ in history if t > now - w * 86400)) / 86400, 1e-9))
        total = sum(rows) * 100
        out[f"d{w}"] = {"n": len(rows), "sum_pct": round(total, 3), "apr_pct": round(total / span * 365, 1),
                        "positive_share": round(sum(r > 0 for r in rows) / len(rows), 2),
                        "net_pct": round(total - roundtrip_cost_pct, 3)}
    worst = None
    for i, (t0, _) in enumerate(history):
        s = sum(r for t, r in history[i:] if t < t0 + 86400) * 100
        worst = s if worst is None else min(worst, s)
    out["worst_24h_pct"] = round(worst, 4)
    return out


def verdict(stats, min_apr=15.0, min_positive=0.8, worst_day_pct=-0.3):
    """'candidato' si en 7 y 30 días pagó con APR >= min_apr, casi siempre positivo, neto > 0 y
    ningún día del historial costó más de worst_day_pct (un solo día malo borra semanas de cobro)."""
    if not stats or not stats.get("d7"):
        return "sin datos"
    d7, d30 = stats["d7"], stats.get("d30") or stats["d7"]
    if (d7["apr_pct"] >= min_apr and d30["apr_pct"] >= min_apr and d7["positive_share"] >= min_positive
            and d30["positive_share"] >= min_positive and d7["net_pct"] > 0
            and stats["worst_24h_pct"] > worst_day_pct):
        return "candidato"
    if d7["apr_pct"] <= 0 or d7["positive_share"] < 0.5:
        return "descartado"
    return "inestable"


def mexc_perp_books():
    """{par: (bid1, ask1)} de todos los perpetuos USDT de MEXC."""
    out = {}
    for t in _get(f"{MEXC_C}/ticker").get("data") or []:
        try:
            out[t["symbol"]] = (float(t["bid1"]), float(t["ask1"]))
        except (TypeError, ValueError, KeyError):
            pass
    return out
