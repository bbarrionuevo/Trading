"""Escáner de desvíos de paridad en stablecoins (nicho de libros finos).

Valor justo en EUR: stablecoins en euro = 1 EUR (anclado por la conversión 1:1 gratuita EUR<->EURC
de Kraken); stablecoins en dólar = precio medio USD/EUR. Si alguien ofrece comprar por encima del
valor justo (o vende por debajo) más allá de la comisión, hay ganancia casi sin riesgo de mercado.
El límite es la profundidad: suelen ser decenas o cientos de EUR, justo la escala de este capital.
"""
EUR_STABLES = {"EURC", "EURR", "EURQ", "EURCV", "EUROP", "EURT", "EURI", "EURS", "EURE", "AEUR"}
USD_STABLES = {"USD", "USDC", "USDT", "USDQ", "USDG", "RLUSD", "PYUSD", "DAI", "USDS", "FDUSD",
               "TUSD", "USD1", "USDE", "USDR", "GUSD", "USDP"}


# Stablecoins que perdieron la paridad: no se tratan como valor justo (hackeo StablR, may-2026;
# redención suspendida). Un descuento ahí es riesgo de crédito, no arbitraje.
DEPEGGED = {"EURR", "USDR"}
DEPEG_THRESHOLD_PCT = 5.0


def usd_in_eur(book):
    for k in ("EUR/USD", "EURC/USD", "EURC/USDC"):
        if k in book:
            b, a = book[k][2], book[k][3]
            return 2 / (b + a)
    return None


def value_eur(asset, usd_eur):
    if asset == "EUR" or asset in EUR_STABLES:
        return 1.0
    if asset in USD_STABLES:
        return usd_eur
    return None


def scan_peg(book, rebalance_fee_pct=0.2):
    """Devuelve desvíos ordenados por ganancia neta estimada (%), con profundidad en EUR."""
    ue = usd_in_eur(book)
    if not ue:
        return []
    out = []
    for pair, v in book.items():
        base, quote, bid, ask, fee = v[:5]
        if "(conv)" in pair:
            continue
        vb, vq = value_eur(base, ue), value_eur(quote, ue)
        if not vb or not vq or {base, quote} <= {"EUR", "USD"} or {base, quote} & DEPEGGED:
            continue
        fair = vb / vq
        dq = vq  # EUR por unidad de quote
        bid_d = v[5] * dq if len(v) > 5 and v[5] else None
        ask_d = v[6] * dq if len(v) > 6 and v[6] else None
        sell_dev = (bid / fair - 1) * 100       # alguien compra base por encima del justo
        buy_dev = (fair / ask - 1) * 100        # alguien vende base por debajo del justo
        for side, dev, depth in (("sell", sell_dev, bid_d), ("buy", buy_dev, ask_d)):
            if abs(dev) > DEPEG_THRESHOLD_PCT:
                continue  # desvío tan grande indica depeg o libro roto, no arbitraje
            out.append({"pair": pair, "side": side, "dev_pct": round(dev, 4),
                        "net_pct": round(dev - fee - rebalance_fee_pct, 4), "fee": fee,
                        "depth_eur": round(depth, 2) if depth else None,
                        "profit_eur_at_depth": round(min(depth or 0, 100) * (dev - fee - rebalance_fee_pct) / 100, 3)})
    return sorted(out, key=lambda x: x["net_pct"], reverse=True)
