"""Motor de descubrimiento: toma muestras de todas las clases de oportunidad y las guarda en JSONL.

python -m nexus.discover OUT.jsonl [samples] [interval_s]
"""
import json
import sys
import time
from pathlib import Path

from .core.ars_scanner import scan
from .discovery import sources
from .discovery.cross import cross
from .discovery.funding import scan_funding
from .discovery.hedge import scan_hedge
from .discovery.maker import scan_maker
from .discovery.peg import scan_peg, usd_in_eur
from .discovery.triangular import cycles

TAKER = {"kraken": 0.40, "bitstamp": 0.40, "coinbase": 0.60}


def _t(msg, t0):
    print(f"  {msg}: {time.time() - t0:.1f}s", flush=True)


def sample():
    rec, errs, t0 = {"ts": time.time()}, {}, time.time()
    try:
        kb = sources.kraken_book()
        rec["kraken_pairs"] = len(kb)
        for start in ("EUR", "USDC"):
            raw = cycles(kb, start)
            rec[f"tri_{start}_raw_top"] = raw[:3]                       # sin filtro (diagnóstico)
            cs = cycles(kb, start, max_spread_pct=1.0, min_vol_quote=5000)
            rec[f"tri_{start}"] = cs[:5]
            rec[f"tri_{start}_maxgross"] = cs and max(c["gross_pct"] for c in cs)
        _t(f"kraken ({len(kb)} pares)", t0)
        ue = usd_in_eur(kb)
        q_eur = {"EUR": 1.0, "USD": ue, "USDC": ue, "USDT": ue, "EURC": 1.0}
        rows = [(p, v[2], v[3], v[7] * q_eur[v[1]]) for p, v in kb.items()
                if v[1] in q_eur and len(v) > 7 and v[7] is not None and "(conv)" not in p]
        rec["maker_kraken"] = scan_maker(rows, 0.25)[:10]
        peg = scan_peg(kb)
        rec["peg_kraken"] = [p for p in peg if p["net_pct"] > -0.5][:10]
        kraken_eur = {p: (v[2], v[3]) for p, v in kb.items() if v[1] == "EUR" and "(conv)" not in p}
        rec["eurc_peg"] = {p: {"bid": v[2], "ask": v[3], "fee": v[4]} for p, v in kb.items()
                           if v[0] == "EURC" and "(conv)" not in p}
    except Exception as e:
        errs["kraken"], kraken_eur = str(e)[:200], {}
    try:
        bb = sources.binance_book()
        rec["binance_pairs"] = len(bb)
        try:
            ue_b = usd_in_eur(kb)
        except Exception:
            ue_b = 0.89
        rec["maker_binance"] = scan_maker(sources.binance_24h({"USDT": ue_b, "USDC": ue_b, "FDUSD": ue_b,
                                                              "EUR": 1.0}), 0.075)[:10]
        rec["peg_binance"] = [p for p in scan_peg(bb, 0.075) if p["net_pct"] > -0.3][:10]
        for start in ("USDT", "BTC", "ARS", "EUR"):
            cs = cycles(bb, start, max_spread_pct=1.0)
            rec[f"bin_tri_{start}"] = cs[:5]
            rec[f"bin_tri_{start}_maxgross"] = cs and max(c["gross_pct"] for c in cs)
    except Exception as e:
        errs["binance"] = str(e)[:200]
    _t("binance", t0)
    books = {"kraken": kraken_eur}
    for name, fn in (("bitstamp", sources.bitstamp_book), ("coinbase", sources.coinbase_book)):
        try:
            books[name] = fn()
        except Exception as e:
            errs[name] = str(e)[:200]
    _t("bitstamp+coinbase", t0)
    # Market making cubierto: cotizar en MEXC (maker 0%) y cubrir en Binance (taker 0,1% / 0,075% con BNB)
    mb = {}
    try:
        mb = sources.mexc_book_all()
        bb = sources.binance_book_all()
        for fee, key in ((0.1, "hedge_mexc_binance"), (0.075, "hedge_mexc_binance_bnb")):
            h = scan_hedge(mb, bb, 0.0, fee)
            rec[key] = h[:30]
            rec[key + "_positive"] = sum(x["net_pct"] > 0 for x in h)
        rec["hedge_common_pairs"] = len(set(mb) & set(bb))
    except Exception as e:
        errs["hedge"] = str(e)[:200]
    _t("cobertura MEXC-Binance", t0)
    # Carry de funding (contado + corto perpetuo) en MEXC y Gate
    frows = []
    for name, fn in (("mexc_funding", sources.mexc_funding), ("gate_funding", sources.gate_funding)):
        try:
            frows += fn()
        except Exception as e:
            errs[name] = str(e)[:200]
    if frows:
        f = scan_funding(frows)
        try:
            spot = {"MEXC": {k[:-4] + "_USDT" for k in mb if k.endswith("USDT")},
                    "Gate": sources.gate_spot_pairs()}
        except Exception as e:
            spot = {}
            errs["gate_spot"] = str(e)[:200]
        for x in f:                      # el carry necesita contado del mismo activo en el mismo exchange
            x["spot_same_exchange"] = x["pair"] in spot.get(x["exchange"], set())
        rec["funding"] = f[:40]
        rec["funding_count"] = len(f)
        rec["funding_top_apr"] = [x["apr_pct"] for x in f[:5]]
    _t("funding", t0)
    books = {k: v for k, v in books.items() if v}
    # Paridad EURC: vender EURC por encima de 1 (o comprar debajo) y convertir 1:1 gratis en Kraken
    peg = []
    for ex, b in books.items():
        if "EURC/EUR" in b:
            bid, ask = b["EURC/EUR"]
            f = TAKER[ex] / 100
            peg.append({"venue": ex, "sell_net_pct": (bid * (1 - f) - 1) * 100,
                        "buy_net_pct": (1 / (ask * (1 + f)) - 1) * 100})
    rec["eurc_arb"] = [{"net_pct": max(x["sell_net_pct"], x["buy_net_pct"]), **x} for x in peg]
    if len(books) > 1:
        rec["cross_eur"] = cross(books, TAKER)[:5]
    for coin in ("USDT", "USDC", "BTC", "ETH"):
        try:
            q = sources.criptoya(coin)
            ops = scan(q, 160000, {"BTC": 0.00002, "ETH": 0.0005}.get(coin, 1.0), 0.0, now=time.time())
            rec[f"ars_{coin}"] = [{k: (str(v) if not isinstance(v, (str, bool)) else v)
                                   for k, v in vars(o).items()} for o in ops[:3]]
        except Exception as e:
            errs[f"ars_{coin}"] = str(e)[:200]
    _t("criptoya", t0)
    rec["errors"] = errs
    return rec


def main(out="data/discovery.jsonl", samples=1, interval=30):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    for i in range(int(samples)):
        rec = sample()
        with open(out, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        print(i, "errores:", rec["errors"] or "ninguno")
        if i < int(samples) - 1:
            time.sleep(float(interval))


if __name__ == "__main__":
    main(*sys.argv[1:4])
