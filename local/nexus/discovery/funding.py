"""Carry de funding: comprar contado y vender el perpetuo (neutral al precio) para cobrar el funding.

Funding positivo = los largos del perpetuo pagan a los cortos cada 8 h. Con contado + corto 1x en el
mismo exchange, el precio no importa (salvo liquidación del corto si el precio se dispara y el
margen no alcanza). El costo es entrar y salir de las dos patas. Lo que decide si sirve es la
PERSISTENCIA del funding: por eso se registra cada muestra y se mide en el tiempo.
"""


def annualize(rate_per_period, periods_per_day=3):
    return rate_per_period * periods_per_day * 365 * 100


def scan_funding(rows, roundtrip_cost_pct=0.2, hold_days=7, min_volume_usdt=1_000_000):
    """rows: [(exchange, simbolo, funding_por_periodo, periodos_por_dia, volumen_24h_usdt)]."""
    out = []
    for ex, sym, rate, per_day, vol in rows:
        if rate is None or vol is None or vol < min_volume_usdt:
            continue
        gross = rate * per_day * hold_days * 100                  # % cobrado en el plazo si persiste
        out.append({"exchange": ex, "pair": sym, "rate_pct": round(rate * 100, 5),
                    "apr_pct": round(annualize(rate, per_day), 1), "vol24h_usdt": round(vol),
                    "net_pct": round(gross - roundtrip_cost_pct, 4), "hold_days": hold_days})
    return sorted(out, key=lambda x: x["net_pct"], reverse=True)
