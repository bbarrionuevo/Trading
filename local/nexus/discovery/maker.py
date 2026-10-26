"""Escáner de market making: pares donde el spread paga ambas comisiones maker con margen.

Un maker compra en el bid y vende en el ask; gana spread - 2*fee_maker por ida y vuelta.
El riesgo es la selección adversa (te llenan justo antes de que el precio se mueva), por eso
exige volumen mínimo y reporta cuántas vueltas por día haría falta para ganar algo relevante.
"""


def scan_maker(rows, maker_fee_pct, min_vol_quote_eur=20000, margin_pct=0.05, max_spread_pct=5.0):
    """rows: [(par, bid, ask, vol24h_en_eur)]."""
    out = []
    for pair, bid, ask, vol in rows:
        if bid <= 0 or ask <= 0 or vol is None or vol < min_vol_quote_eur:
            continue
        spread = (ask / bid - 1) * 100
        if spread > max_spread_pct:
            continue  # spread absurdo = libro roto o sin operaciones reales
        edge = spread - 2 * maker_fee_pct
        if edge > margin_pct:
            out.append({"pair": pair, "spread_pct": round(spread, 3), "edge_pct": round(edge, 3),
                        "vol24h_eur": round(vol), "eur_per_100_roundtrip": round(edge, 3)})
    return sorted(out, key=lambda x: x["edge_pct"] * min(x["vol24h_eur"], 1e6), reverse=True)
