"""Argentina: P2P, apps y dólar con datos públicos de CriptoYa (sin claves).

Tres oportunidades distintas de comprar en un lado y vender en otro como tomador (eso dio negativo):
1. Spread P2P: quien PUBLICA avisos de compra y de venta en una plataforma P2P gana la diferencia
   entre el mejor aviso de venta (ask) y el mejor de compra (bid). Es el negocio del "merchant".
2. Aviso P2P cubierto: publicar un aviso de compra en P2P y, al llenarse, vender al instante en la
   app/exchange que mejor paga (o al revés). Gana si el aviso queda por debajo de lo que paga la app.
3. "Rulo" dólar oficial -> USDT -> pesos: comprar dólares en el banco, pasarlos a una plataforma que
   acepte dólares y los convierta a USDT, y vender ese USDT por pesos al precio cripto.
4. Vuelta internacional (ventaja de tener cuentas afuera): comprar USDT en el exterior (~1 USD),
   venderlo por pesos en Argentina, comprar dólares oficiales y transferirlos de vuelta al exterior.
   Existe porque dentro de Argentina pasar dólares a USDT cuesta varios % (fricción local).
Todo se informa como oportunidad A VERIFICAR: comisiones reales, cupos, tiempos y normativa varían.
"""
import time

import requests

CY = "https://criptoya.com/api"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
P2P_FEE_PCT = 0.2          # comisión típica del que publica avisos (conservador; varía por plataforma)
USD_TO_USDT_COST_PCT = 1.0  # costo de pasar dólares bancarios a USDT (conservador; a verificar)
MAX_AGE_S = 2 * 86400


def _get(path):
    r = S.get(f"{CY}/{path}", timeout=(5, 20))
    r.raise_for_status()
    return r.json()


def fetch(coin="USDT", vols=(100, 1000)):
    out = {"quotes": {v: _get(f"{coin}/ARS/{v}") for v in vols}, "dolar": _get("dolar"),
           "bancos": _get("bancostodos")}
    try:
        out["usd"] = _get(f"{coin}/USD/100")             # cuántos dólares cuesta un USDT en plataformas AR
    except Exception:                                      # noqa: BLE001 (opcional)
        out["usd"] = {}
    for c in ("USDC",):                                    # la otra vía local: dólares -> USDC -> pesos
        try:
            out[f"usd_{c}"] = _get(f"{c}/USD/100")
            out[f"ars_{c}"] = _get(f"{c}/ARS/100")
        except Exception:                                  # noqa: BLE001
            out[f"usd_{c}"], out[f"ars_{c}"] = {}, {}
    return out


def is_p2p(name):
    return name.endswith("p2p") or name in ("p2pme", "eldorado")


def rows(quotes, now=None, max_dev_pct=4.0, ref=None):
    """Cotizaciones frescas y con sentido: [(nombre, ask, totalAsk, bid, totalBid, es_p2p)].
    Descarta precios a más de max_dev_pct de la mediana del mercado (avisos rotos o sin liquidez:
    visto el 24-oct-2026, HTX P2P 1.200/1.957 y MEXC P2P 1.851 con el mercado en ~1.640)."""
    now = now or time.time()
    raw = []
    for name, q in quotes.items():
        try:
            ask, bid = float(q["ask"]), float(q["bid"])
            t_ask, t_bid = float(q.get("totalAsk") or ask), float(q.get("totalBid") or bid)
        except (KeyError, TypeError, ValueError):
            continue
        if min(ask, bid, t_ask, t_bid) <= 0 or now - float(q.get("time") or now) > MAX_AGE_S:
            continue
        raw.append((name, ask, t_ask, bid, t_bid, is_p2p(name)))
    if not raw:
        return []
    if ref is None:                                    # sin referencia externa: mediana del mercado
        mids = sorted((r[1] + r[3]) / 2 for r in raw)
        ref = mids[len(mids) // 2] if len(mids) % 2 else (mids[len(mids) // 2 - 1] + mids[len(mids) // 2]) / 2
    return [r for r in raw if all(abs(x / ref - 1) * 100 <= max_dev_pct for x in r[1:5])]


def p2p_spreads(rs, fee_pct=P2P_FEE_PCT):
    """Margen del merchant por plataforma P2P: comprar al mejor bid y vender al mejor ask, publicando."""
    out = []
    for name, ask, _, bid, _, p2p in rs:
        if p2p and ask > bid:
            gross = (ask / bid - 1) * 100
            out.append({"platform": name, "bid": bid, "ask": ask, "gross_pct": round(gross, 3),
                        "net_pct": round(gross - 2 * fee_pct, 3)})
    return sorted(out, key=lambda x: -x["net_pct"])


def hedged_ads(rs, fee_pct=P2P_FEE_PCT):
    """Aviso P2P cubierto contra la mejor app como tomador (incluye las comisiones de la app)."""
    apps = [r for r in rs if not r[5]]
    if not apps:
        return []
    best_sell = max(apps, key=lambda r: r[4])                 # quien más paga por tu USDT (totalBid)
    best_buy = min(apps, key=lambda r: r[2])                  # quien más barato vende USDT (totalAsk)
    out = []
    for name, ask, _, bid, _, p2p in rs:
        if not p2p:
            continue
        out.append({"platform": name, "side": "aviso de COMPRA en P2P, venta en app", "ad_price": bid,
                    "hedge_at": best_sell[0], "hedge_price": best_sell[4],
                    "net_pct": round((best_sell[4] / bid - 1) * 100 - fee_pct, 3)})
        out.append({"platform": name, "side": "aviso de VENTA en P2P, compra en app", "ad_price": ask,
                    "hedge_at": best_buy[0], "hedge_price": best_buy[2],
                    "net_pct": round((ask / best_buy[2] - 1) * 100 - fee_pct, 3)})
    return sorted(out, key=lambda x: -x["net_pct"])


def fresh_banks(bancos, now=None):
    now = now or time.time()
    out = []
    for name, q in bancos.items():
        try:
            if now - float(q.get("time") or 0) <= MAX_AGE_S and float(q["totalAsk"]) > 0:
                out.append((name, float(q["totalAsk"]), float(q["totalBid"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x[1])


def usd_to_usdt(usd_quotes, now=None):
    """(plataforma, dólares por USDT) más barata para pasar dólares a USDT, o None (sin dato)."""
    rs = rows(usd_quotes or {}, now, max_dev_pct=12.0, ref=1.0)     # USDT ~ 1 USD; la prima local es real
    if not rs:
        return None
    best = min(rs, key=lambda r: r[2])
    return best[0], best[2]


def rulo(dolar, bancos, rs, usd_quotes=None, conv_cost_pct=USD_TO_USDT_COST_PCT, now=None):
    """Pesos -> dólares (banco oficial o MEP) -> USDT -> pesos, con el costo REAL de pasar dólares
    a USDT si hay cotizaciones USDT/USD (si no, se supone conv_cost_pct)."""
    banks = fresh_banks(bancos, now)
    if not rs:
        return None
    best = max(rs, key=lambda r: r[4])
    conv = usd_to_usdt(usd_quotes, now)
    usdt_per_usd_cost = conv[1] if conv else 1 + conv_cost_pct / 100
    routes = []
    if banks:
        routes.append(("oficial (" + banks[0][0] + ")", banks[0][1]))
    mep = _mep(dolar)
    if mep:
        routes.append(("MEP (AL30)", mep))
    out = []
    for label, usd_ars in routes:
        net = (best[4] / (usd_ars * usdt_per_usd_cost) - 1) * 100
        out.append({"route": label, "usd_ars": usd_ars, "conv_at": conv[0] if conv else "supuesto",
                    "usd_per_usdt": round(usdt_per_usd_cost, 4), "sell_at": best[0], "usdt_bid": best[4],
                    "gross_pct": round((best[4] / usd_ars - 1) * 100, 3), "net_pct": round(net, 3)})
    return {"routes": out, "mep": mep, "oficial": (dolar.get("oficial") or {}).get("ask"),
            "blue": (dolar.get("blue") or {}).get("bid"),
            "cripto_usdt": ((dolar.get("cripto") or {}).get("usdt") or {})}


def _mep(dolar):
    """Precio MEP de referencia (AL30, contado inmediato si está), o None."""
    try:
        al30 = dolar["mep"]["al30"]
        for k in ("ci", "24hs"):
            if k in al30 and al30[k].get("price"):
                return float(al30[k]["price"])
    except (KeyError, TypeError, ValueError):
        pass
    return None


def kraken_usdt_usd():
    """Precio de compra (ask) de USDT con USD en Kraken, o None."""
    r = S.get("https://api.kraken.com/0/public/Ticker", params={"pair": "USDTUSD"}, timeout=(5, 15)).json()
    for v in (r.get("result") or {}).values():
        return float(v["a"][0])
    return None


def cross_border(rs, bancos, usdt_usd_ask, buy_fee_pct=0.26, network_pct=0.1, wire_pct=1.0, now=None,
                 mep=None, wire_fixed_usd=40.0, wire_var_pct=0.3, sizes=(1_000, 5_000, 10_000)):
    """USD afuera -> USDT (Kraken) -> pesos (mejor totalBid) -> dólares (banco oficial o MEP por broker)
    -> USD afuera. El giro al exterior tiene un costo FIJO (comisión del banco + corresponsal, supuesto
    wire_fixed_usd) más un %: con montos chicos el fijo se come la ganancia. Se informa el neto por
    vuelta para varios tamaños de capital. Todos los costos del giro son A VERIFICAR con tu banco."""
    banks = fresh_banks(bancos, now)
    if not rs or not banks or not usdt_usd_ask:
        return None
    best = max(rs, key=lambda r: r[4])
    cost_usd = usdt_usd_ask * (1 + buy_fee_pct / 100)
    gross = (best[4] / banks[0][1] / cost_usd - 1) * 100
    out = {"usdt_usd_ask": usdt_usd_ask, "sell_at": best[0], "usdt_bid": best[4], "bank": banks[0][0],
           "usd_ars": banks[0][1], "gross_pct": round(gross, 3),
           "net_pct": round(gross - network_pct - wire_pct, 3), "wire_pct": wire_pct,
           "wire_fixed_usd": wire_fixed_usd, "wire_var_pct": wire_var_pct}
    out["by_size"] = {n: round(gross - network_pct - wire_var_pct - wire_fixed_usd / n * 100, 2) for n in sizes}
    if mep:
        g_mep = (best[4] / mep / cost_usd - 1) * 100
        out["mep_gross_pct"] = round(g_mep, 3)
        out["mep_by_size"] = {n: round(g_mep - network_pct - wire_var_pct - wire_fixed_usd / n * 100, 2) for n in sizes}
    return out


def usd_conversion_table(usd_quotes_by_coin, now=None):
    """Cuánto cuesta en cada plataforma argentina pasar dólares a una stablecoin (USD por unidad).
    Es la fricción que mantiene la brecha: si alguna baja de ~1,02, el rulo queda dentro del país."""
    out = []
    for coin, quotes in usd_quotes_by_coin.items():
        for r in rows(quotes or {}, now, max_dev_pct=15.0, ref=1.0):
            out.append({"coin": coin, "platform": r[0], "usd_per_unit": round(r[2], 4),
                        "premium_pct": round((r[2] - 1) * 100, 2)})
    return sorted(out, key=lambda x: x["usd_per_unit"])


def local_loops(conv, ars_bids_by_coin, usd_ars_routes):
    """Rulo SIN salir del país para cada plataforma de conversión: pesos -> dólares (ruta) -> stablecoin
    (plataforma) -> pesos (mejor bid de esa moneda). Neto % por vuelta."""
    out = []
    for c in conv:
        bid = ars_bids_by_coin.get(c["coin"])
        if not bid:
            continue
        for label, usd_ars in usd_ars_routes:
            out.append({"route": label, "convert_at": c["platform"], "coin": c["coin"], "sell_at": bid[0],
                        "net_pct": round((bid[1] / (usd_ars * c["usd_per_unit"]) - 1) * 100, 3)})
    return sorted(out, key=lambda x: -x["net_pct"])
