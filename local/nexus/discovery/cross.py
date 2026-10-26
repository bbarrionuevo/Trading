"""Arbitraje entre exchanges con inventario pre-posicionado (sin transferir en cada operación).

books: {exchange: {par: (bid, ask)}}; fees: {exchange: taker_pct}.
Compra en A al ask, vende en B al bid en el mismo instante; el rebalanceo se hace raramente.
"""


def cross(books, fees):
    out = []
    pairs = set().union(*[b.keys() for b in books.values()])
    for p in pairs:
        venues = [(ex, b[p]) for ex, b in books.items() if p in b]
        for a, (_, ask) in venues:
            for b, (bid, _) in venues:
                if a == b or ask <= 0:
                    continue
                gross = bid / ask - 1
                net = bid * (1 - fees[b] / 100) / (ask * (1 + fees[a] / 100)) - 1
                out.append({"pair": p, "buy": a, "sell": b, "gross_pct": gross * 100, "net_pct": net * 100})
    return sorted(out, key=lambda x: x["net_pct"], reverse=True)
