"""Arbitraje triangular dentro de un exchange (sin tocar bancos ni redes).

Entrada: libro top-of-book {par: (base, quote, bid, ask, fee_pct)}.
Recorre ciclos A->B->C->A empezando en `start` y devuelve el rendimiento neto de cada uno.
"""


def _edges(book, max_spread_pct=None, min_vol_quote=None):
    """Arista (desde, hacia) -> (tasa, fee, par). Vender base da bid; comprar base cuesta ask.

    Descarta pares con spread excesivo o volumen 24h bajo (libros vacíos o precios viejos).
    """
    e = {}
    for pair, (base, quote, bid, ask, fee, *extra) in book.items():
        if bid <= 0 or ask <= 0:
            continue
        if max_spread_pct is not None and (ask / bid - 1) * 100 > max_spread_pct:
            continue
        vol = extra[2] if len(extra) > 2 else None
        if min_vol_quote is not None and vol is not None and vol < min_vol_quote:
            continue
        if bid > 0:
            e[(base, quote)] = (bid, fee, pair)
        if ask > 0:
            e[(quote, base)] = (1 / ask, fee, pair)
    return e


def depth(book, pair):
    v = book[pair]
    return (v[5], v[6]) if len(v) > 6 else (None, None)


def cycles(book, start="EUR", fee_override=None, max_spread_pct=None, min_vol_quote=None):
    e = _edges(book, max_spread_pct, min_vol_quote)
    adj = {}
    for a, b in e:
        adj.setdefault(a, set()).add(b)
    out = []
    for b in adj.get(start, ()):
        for c in adj.get(b, ()):
            if c == start or (c, start) not in e:
                continue
            legs = [(start, b), (b, c), (c, start)]
            gross = net = 1.0
            for l in legs:
                rate, fee, _ = e[l]
                fee = fee if fee_override is None else fee_override
                gross *= rate
                net *= rate * (1 - fee / 100)
            out.append({"path": f"{start}>{b}>{c}>{start}", "gross_pct": (gross - 1) * 100,
                        "net_pct": (net - 1) * 100, "pairs": [e[l][2] for l in legs],
                        "depth": [depth(book, e[l][2]) for l in legs]})
    return sorted(out, key=lambda x: x["net_pct"], reverse=True)
