"""Arbitraje entre plataformas argentinas (solo ARS, sin tocar rieles europeos).

Usa la respuesta de CriptoYa /api/{coin}/ARS/{vol}: {exchange: {totalAsk, totalBid, ...}}
totalAsk = ARS que cuesta comprar 1 unidad (con comisiones); totalBid = ARS netos al vender 1.
"""
from dataclasses import dataclass
from decimal import Decimal as D


@dataclass(frozen=True)
class CrossOpp:
    buy_at: str
    sell_at: str
    gross_pct: D
    net_pct: D
    net_profit_ars: D
    executable: bool


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def scan(quotes: dict, capital_ars, transfer_fee_coin, min_net_pct=0.3, max_age_s=120,
         now=None, allowed=None, outlier_pct=1.5):
    """Devuelve oportunidades ordenadas por ROI neto desc.

    transfer_fee_coin: costo fijo de mover la moneda entre plataformas (en unidades de la moneda).
    """
    cap, fee, out = D(str(capital_ars)), D(str(transfer_fee_coin)), []
    clean = {}
    for ex, q in quotes.items():
        try:
            ask, bid = D(str(q["totalAsk"])), D(str(q["totalBid"]))
        except (KeyError, TypeError, ValueError):
            continue
        if ask <= 0 or bid <= 0 or (allowed and ex not in allowed):
            continue
        if now and q.get("time") and now - q["time"] > max_age_s:
            continue  # cotización vieja
        clean[ex] = (ask, bid)
    if len(clean) >= 3:  # descarta cotizaciones atípicas (P2P ilíquido o erróneo)
        ma, mb = _median([v[0] for v in clean.values()]), _median([v[1] for v in clean.values()])
        tol = D(str(outlier_pct)) / 100
        clean = {k: v for k, v in clean.items()
                 if abs(v[0] / ma - 1) <= tol and abs(v[1] / mb - 1) <= tol}
    for a, (ask_a, _) in clean.items():
        for b, (_, bid_b) in clean.items():
            if a == b:
                continue
            units = cap / ask_a - fee            # compra en A, paga red
            final = units * bid_b
            profit = final - cap
            net = profit / cap * 100
            out.append(CrossOpp(a, b, (bid_b / ask_a - 1) * 100, net, profit, net > D(str(min_net_pct))))
    return sorted(out, key=lambda o: o.net_pct, reverse=True)
