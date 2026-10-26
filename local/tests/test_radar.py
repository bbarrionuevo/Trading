"""Radar de oportunidades: P2P/dólar Argentina, funding entre exchanges y paper de diferencias."""
import time

NOW = 1_800_000_000


def q(ask, bid, t_ask=None, t_bid=None, age=0):
    return {"ask": ask, "bid": bid, "totalAsk": t_ask or ask, "totalBid": t_bid or bid, "time": NOW - age}


def test_p2p_descarta_precios_rotos_y_viejos():
    from nexus.discovery.p2p_ar import rows
    quotes = {"fiwind": q(1650, 1637), "binancep2p": q(1643, 1637), "huobip2p": q(1957, 1200),
              "viejo": q(1640, 1630, age=10 * 86400), "mexcp2p": q(1855, 1851), "lemoncashp2p": q(1648, 1637)}
    names = {r[0] for r in rows(quotes, now=NOW)}
    assert names == {"fiwind", "binancep2p", "lemoncashp2p"}


def test_spread_p2p_y_aviso_cubierto():
    from nexus.discovery.p2p_ar import hedged_ads, p2p_spreads, rows
    rs = rows({"eldoradop2p": q(1660, 1630), "binancep2p": q(1643, 1637), "fiwind": q(1650, 1637, 1652, 1637),
               "bitsoalpha": q(1648, 1640, 1649, 1638)}, now=NOW)
    sp = p2p_spreads(rs)
    assert sp[0]["platform"] == "eldoradop2p" and round(sp[0]["gross_pct"], 2) == round((1660 / 1630 - 1) * 100, 2)
    h = hedged_ads(rs)
    top = h[0]
    assert top["platform"] == "eldoradop2p" and top["side"].startswith("aviso de VENTA") and top["hedge_at"] == "bitsoalpha"
    assert abs(top["net_pct"] - ((1660 / 1649 - 1) * 100 - 0.2)) < 1e-3


def test_rulo_usa_costo_real_de_dolar_a_usdt_y_vuelta_internacional():
    from nexus.discovery.p2p_ar import cross_border, rows, rulo
    rs = rows({"okexp2p": q(1645, 1641), "fiwind": q(1650, 1637)}, now=NOW)
    bancos = {"bna": q(1550, 1500), "prex": q(1538, 1490), "viejo": q(1300, 1250, age=90 * 86400)}
    usd = {"fiwind": q(1.065, 1.0), "roto": q(5, 4)}
    dolar = {"oficial": {"ask": 1550}, "mep": {"al30": {"ci": {"price": 1563.45}}}, "blue": {"bid": 1545}}
    r = rulo(dolar, bancos, rs, usd, now=NOW)
    of, mep = r["routes"]
    assert of["route"].startswith("oficial (prex)") and of["usd_per_usdt"] == 1.065
    assert abs(of["net_pct"] - ((1641 / (1538 * 1.065) - 1) * 100)) < 1e-3 and mep["net_pct"] < of["net_pct"]
    cb = cross_border(rs, bancos, 0.9987, now=NOW)
    assert abs(cb["gross_pct"] - ((1641 / 1538 / (0.9987 * 1.0026) - 1) * 100)) < 1e-3
    assert cb["net_pct"] == round(cb["gross_pct"] - 0.1 - 1.0, 3)


def test_funding_normaliza_nombres_intervalos_y_separa_extremos():
    from nexus.discovery import funding_multi as fm
    assert [fm.norm_base(x) for x in ("kPEPE", "1000SATS", "KAITO", "XBT", "1INCH")] == ["PEPE", "SATS", "KAITO", "BTC", "1INCH"]
    assert round(fm.apr(0.0001, 8), 2) == 10.95 and round(fm.apr(0.0001, 1), 1) == 87.6
    rows = [fm.row("OKX", "IRYS", -0.0002, 4, 5e6, symbol="IRYS-USDT-SWAP", px=1.0),
            fm.row("MEXC", "IRYS", 0.0000, 4, 5e6, symbol="IRYS_USDT", px=1.001),
            fm.row("KuCoin", "NEW", 0.02, 4, 5e6), fm.row("Gate", "NEW", 0.0, 4, 5e6),
            fm.row("Bitget", "TINY", 0.01, 4, 10), fm.row("OKX", "TINY", 0.0, 4, 10)]
    x = fm.cross_spreads(rows)
    assert {c["base"] for c in x} == {"IRYS", "NEW"}                       # TINY: sin volumen
    irys = next(c for c in x if c["base"] == "IRYS")
    assert irys["short"] == "MEXC" and irys["long"] == "OKX" and not irys["extreme"] and irys["short_px"] == 1.001
    assert next(c for c in x if c["base"] == "NEW")["extreme"]


def test_lo_realmente_cobrado_entre_dos_exchanges():
    from nexus.discovery.funding_multi import realized_spread
    short = [(NOW + h * 3600, 0.0001) for h in range(0, 24 * 7, 4)]       # corto cobra 0,01% cada 4 h
    long = [(NOW + h * 3600, -0.0002) for h in range(0, 24 * 7, 4)]       # largo cobra 0,02% (tasa negativa)
    total, apr, pos, span = realized_spread(short, long)
    assert abs(total - 42 * 0.03) < 1e-6 and pos == 1.0 and 6.5 < span <= 7
    assert realized_spread([], long) is None


def test_paper_de_diferencias_abre_solo_lo_que_ya_pago_y_acumula_cobros():
    from nexus import paper_xfunding as px
    base = {"short": "MEXC", "long": "OKX", "short_symbol": "IRYS_USDT", "long_symbol": "IRYS-USDT-SWAP",
            "short_px": 1.001, "long_px": 1.0, "cost_pct": 0.14}
    cross = [base | {"base": "IRYS", "realized": (2.39, 128.0, 1.0, 6.8)},
             base | {"base": "PROS", "realized": (0.1, 5.0, 0.71, 6.8)},           # pagó poco: no entra
             base | {"base": "WIDE", "realized": (3.0, 150.0, 1.0, 6.8), "short_px": 1.02}]  # base 2%: no entra
    hist = {}
    st = px.step({}, cross, lambda ex, sym: hist.get((ex, sym), []), now=NOW)
    assert list(st["open"]) == ["IRYS:MEXC/OKX"]
    hist[("MEXC", "IRYS_USDT")] = [(NOW - 3600, 0.01), (NOW + 4 * 3600, 0.0001)]   # el previo no cuenta
    hist[("OKX", "IRYS-USDT-SWAP")] = [(NOW + 4 * 3600, -0.0002)]
    st = px.step(st, cross, lambda ex, sym: hist.get((ex, sym), []), now=NOW + 5 * 3600)
    p = st["open"]["IRYS:MEXC/OKX"]
    assert p["settlements"] == 2 and abs(p["funding"] - 50 * 0.0003) < 1e-12
    cross[0] = cross[0] | {"short": "Gate", "realized": (-0.5, -20.0, 0.3, 6.8)}      # otra combinación: no la cierra
    st = px.step(st, cross, lambda ex, sym: hist.get((ex, sym), []), now=NOW + 6 * 3600)
    assert "IRYS:MEXC/OKX" in st["open"]
    hist[("MEXC", "IRYS_USDT")] += [(NOW + 20 * 3600, -0.001)]                     # SUS patas dejan de pagar
    st = px.step(st, cross, lambda ex, sym: hist.get((ex, sym), []), now=NOW + 25 * 3600)
    assert st["open"] == {} and st["closed"][0]["base"] == "IRYS" and "IRYS (cerrada)" in px.to_markdown(st)


def test_informe_del_radar_en_espanol():
    from nexus.radar import history_line, persistence, to_markdown
    res = {"ts": NOW, "rulo": {"routes": [{"route": "oficial (bna)", "usd_ars": 1550, "conv_at": "fiwind",
                                           "usd_per_usdt": 1.065, "sell_at": "okexp2p", "usdt_bid": 1641,
                                           "gross_pct": 5.9, "net_pct": -0.5}]},
           "cross_border": {"usdt_usd_ask": 1.0, "sell_at": "okexp2p", "usdt_bid": 1641, "bank": "bna",
                            "usd_ars": 1550, "gross_pct": 5.6, "net_pct": 4.5, "wire_pct": 1.0},
           "cross_funding": [{"base": "IRYS", "short": "MEXC", "short_apr": 10.5, "long": "OKX", "long_apr": -148.6,
                              "spread_apr": 159.1, "cost_pct": 0.14, "breakeven_days": 0.3, "intervals_known": True,
                              "realized": (2.39, 128.0, 1.0, 6.8), "net_hold_pct": 3.0}],
           "funding_venues": {"OKX": 1}, "lending": [], "launchpools": [], "events": [], "errors": {}}
    md = to_markdown(res, [history_line(res)] * 3)
    assert "Vuelta internacional" in md and "IRYS" in md and "+128" in md and "Persistencia" in md
    assert persistence([history_line(res)] * 3)[1][0] == ("IRYS", 3, 159.1)


def test_rulo_por_tamano_y_conversion_local():
    from nexus.discovery.p2p_ar import cross_border, local_loops, rows, usd_conversion_table
    rs = rows({"okexp2p": q(1645, 1641), "fiwind": q(1650, 1637)}, now=NOW)
    cb = cross_border(rs, {"bna": q(1550, 1500)}, 1.0, now=NOW, mep=1563.45)
    small, mid, big = cb["by_size"][1000], cb["by_size"][5000], cb["by_size"][10000]
    assert small < mid < big and abs((big - small) - (40 / 1000 * 100 - 40 / 10000 * 100)) < 0.02
    assert cb["mep_gross_pct"] < cb["gross_pct"]
    conv = usd_conversion_table({"USDT": {"fiwind": q(1.065, 1.0), "barato": q(1.015, 0.99)},
                                 "USDC": {"belo": q(1.03, 1.0), "roto": q(9, 8)}}, now=NOW)
    assert [c["platform"] for c in conv] == ["barato", "belo", "fiwind"]
    loops = local_loops(conv, {"USDT": ("okexp2p", 1641), "USDC": ("lemon", 1639)}, [("oficial", 1550)])
    assert loops[0]["convert_at"] == "barato" and abs(loops[0]["net_pct"] - (1641 / (1550 * 1.015) - 1) * 100) < 1e-3


def test_market_making_estable_prioriza_actividad_y_descuenta_comisiones():
    from nexus.discovery.stable_mm import analyze, is_candidate, usd_rates
    assert is_candidate("USDT", "ARS") and is_candidate("USDC", "USDT") and is_candidate("EUR", "USDT")
    assert not is_candidate("BTC", "USDT") and not is_candidate("USDT", "BTC")
    rows = [("Binance", "USDTARS", "USDT", "ARS", 1640.0, 1645.0, 1.0, 1640e6, 100_000),
            ("Binance", "EURIUSDC", "EURI", "USDC", 1.10, 1.105, 0.0001, 6000, 50),          # ancho pero sin actividad
            ("Binance", "USDCUSDT", "USDC", "USDT", 0.9998, 0.9999, 0.0001, 1e9, 500_000)]
    assert abs(usd_rates({("USDT", "ARS"): (1640.0, 1645.0)})["ARS"] - 1 / 1642.5) < 1e-12
    res = analyze(rows)
    assert res[0]["symbol"] == "USDTARS" and abs(res[0]["vol_usd_24h"] - 1640e6 / 1642.5) < 2
    usdc = next(x for x in res if x["symbol"] == "USDCUSDT")
    assert usdc["net_pct"][0.0] > 0 > usdc["net_pct"][0.075]                                  # solo con comisión cero


def test_defi_solo_stablecoins_grandes_sin_perdida_impermanente():
    from nexus.discovery.yields import defi_stable_pools
    raw = {"data": [
        {"project": "aave-v3", "chain": "Arbitrum", "symbol": "USDC", "tvlUsd": 300e6, "apyBase": 5.1,
         "apyReward": 0, "apyMean30d": 4.8, "stablecoin": True, "ilRisk": "no"},
        {"project": "x", "chain": "Base", "symbol": "USDC-USDT", "tvlUsd": 50e6, "apyBase": 9.0,
         "stablecoin": True, "ilRisk": "no", "outlier": True},                                # atípico: fuera
        {"project": "y", "chain": "Ethereum", "symbol": "ETH-USDC", "tvlUsd": 900e6, "apyBase": 20,
         "stablecoin": False, "ilRisk": "yes"},                                               # no estable: fuera
        {"project": "z", "chain": "Ethereum", "symbol": "USDT", "tvlUsd": 5e6, "apyBase": 30,
         "stablecoin": True, "ilRisk": "no"}]}                                                # TVL chico: fuera
    out = defi_stable_pools(raw)
    assert [x["project"] for x in out] == ["aave-v3"] and out[0]["tvl_musd"] == 300.0 and out[0]["bluechip"]


def test_paper_de_funding_una_posicion_por_moneda():
    from nexus import paper_xfunding as px
    base = {"short_symbol": "a", "long_symbol": "b", "short_px": 1.0, "long_px": 1.0, "cost_pct": 0.16,
            "realized": (1.1, 59.0, 1.0, 6.8)}
    cross = [base | {"base": "JCT", "short": "MEXC", "long": "Bitget"}, base | {"base": "JCT", "short": "KuCoin", "long": "Bitget"},
             base | {"base": "LA", "short": "Bitget", "long": "Gate"}]
    st = px.step({}, cross, lambda ex, sym: [], now=NOW)
    assert sorted(p["base"] for p in st["open"].values()) == ["JCT", "LA"]
