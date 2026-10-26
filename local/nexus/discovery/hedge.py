"""Market making cubierto: cotizar en el exchange A (maker barato) y cubrir al instante en B (taker).

Si el mejor bid de A queda por debajo del bid de B menos la comisión taker de B, comprar como maker
en A y vender en el acto en B deja ganancia SIN quedar expuesto al precio (el riesgo principal del
market making simple). Igual del lado vendedor. También mide el "margen para mejorar": cuánto más
arriba del mejor bid de A se podría ofertar y seguir ganando, lo que aumenta la chance de llenado.
Límite: profundidad del mejor nivel en B (lo que se puede cubrir de inmediato).
"""


def scan_hedge(a_book, b_book, a_maker_pct=0.0, b_taker_pct=0.1, a_name="MEXC", b_name="Binance",
               max_dev_pct=3.0):
    """a_book/b_book: {simbolo: (bid, ask, bid_qty, ask_qty)}. Devuelve oportunidades por lado."""
    fa, fb = a_maker_pct / 100, b_taker_pct / 100
    out = []
    for sym in set(a_book) & set(b_book):
        ab, aa = a_book[sym][:2]
        bb, ba, bbq, baq = b_book[sym][:4]
        if min(ab, aa, bb, ba) <= 0:
            continue
        mid_a, mid_b = (ab + aa) / 2, (bb + ba) / 2
        if abs(mid_a / mid_b - 1) * 100 > max_dev_pct:
            continue  # precios demasiado distintos: otro activo con el mismo ticker o mercado roto
        buy_edge = (bb * (1 - fb)) / (ab * (1 + fa)) - 1        # compro maker en A, vendo taker en B
        sell_edge = (aa * (1 - fa)) / (ba * (1 + fb)) - 1       # vendo maker en A, compro taker en B
        for side, edge, depth in (("buy_A_sell_B", buy_edge, bbq * bb), ("sell_A_buy_B", sell_edge, baq * ba)):
            out.append({"pair": sym, "side": side, "route": f"{a_name}->{b_name}",
                        "net_pct": round(edge * 100, 4), "a_spread_pct": round((aa / ab - 1) * 100, 4),
                        "hedge_depth_usdt": round(depth, 2)})
    return sorted(out, key=lambda x: x["net_pct"], reverse=True)
