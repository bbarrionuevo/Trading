"""Market making donde el precio casi no se mueve: moneda local contra stablecoin y stablecoin contra
stablecoin (Binance y MEXC, datos públicos).

Ganancia chica por vuelta (el spread), riesgo de precio bajo (USDT/ARS se mueve con la devaluación,
USDC/USDT casi nada) y repetible miles de veces. El enemigo es la comisión maker y la cola: solo
sirven los pares cuyo spread supera dos comisiones maker con actividad suficiente para llenarse.
Las comisiones reales de cada par se verifican con la clave de solo lectura (tradeFee) en el PC.
"""
import requests

BINANCE = "https://data-api.binance.vision/api/v3"
MEXC = "https://api.mexc.com/api/v3"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP", "PYUSD", "USDE", "EURI", "AEUR", "USD1", "XUSD",
           "BFUSD", "RLUSD", "EURC", "USDS"}
FIATS = {"ARS", "BRL", "TRY", "MXN", "COP", "ZAR", "EUR", "PLN", "UAH", "NGN", "JPY", "IDR", "RON", "CZK",
         "GBP", "AUD", "VND", "PHP", "KZT", "CLP", "PEN", "SAR", "AED", "INR"}
FEES = (0.0, 0.075)                 # maker % supuestos: promoción cero y Binance con BNB


def _get(url, **kw):
    r = S.get(url, timeout=(5, 25), **kw)
    r.raise_for_status()
    return r.json()


def is_candidate(base, quote):
    return (base in STABLES and quote in FIATS | STABLES) or (base in FIATS and quote in STABLES)


def usd_rates(books):
    """USD por unidad de cada moneda cotizada, a partir de los pares USDT/xxx o xxx/USDT."""
    rate = {s: 1.0 for s in STABLES}
    for (base, quote), (bid, ask) in books.items():
        mid = (bid + ask) / 2
        if base == "USDT" and quote in FIATS and mid > 0:
            rate[quote] = 1 / mid
        elif quote == "USDT" and base in FIATS:
            rate[base] = mid
    return rate


def analyze(rows, fees=FEES):
    """rows: [(exchange, símbolo, base, quote, bid, ask, tick, vol_quote_24h, trades_24h)].
    Devuelve métricas por par, ordenadas por neto con la comisión más alta y actividad."""
    books = {(r[2], r[3]): (r[4], r[5]) for r in rows if r[0] == "Binance"}
    rate = usd_rates(books)
    out = []
    for ex, sym, base, quote, bid, ask, tick, volq, n in rows:
        if bid <= 0 or ask <= bid:
            continue
        mid = (bid + ask) / 2
        spread = (ask / bid - 1) * 100
        vol_usd = volq * rate.get(quote, 0)
        out.append({"exchange": ex, "symbol": sym, "pair": f"{base}/{quote}", "bid": bid, "ask": ask,
                    "spread_pct": round(spread, 4), "tick_pct": round(tick / mid * 100, 4) if tick else None,
                    "vol_usd_24h": round(vol_usd), "trades_per_min": round((n or 0) / 1440, 2),
                    "net_pct": {f: round(spread - 2 * f, 4) for f in fees}})
    # primero lo que tiene actividad real: un spread enorme sin operaciones no llena nunca
    return sorted(out, key=lambda x: x["spread_pct"] * min(x["trades_per_min"], 30), reverse=True)


def binance_rows():
    info = _get(f"{BINANCE}/exchangeInfo", params={"permissions": "SPOT"})["symbols"]
    meta = {}
    for s in info:
        if s["status"] != "TRADING" or not is_candidate(s["baseAsset"], s["quoteAsset"]):
            continue
        tick = next((float(f["tickSize"]) for f in s["filters"] if f["filterType"] == "PRICE_FILTER"), None)
        meta[s["symbol"]] = (s["baseAsset"], s["quoteAsset"], tick)
    t24 = {t["symbol"]: t for t in _get(f"{BINANCE}/ticker/24hr") if t["symbol"] in meta}
    out = []
    for t in _get(f"{BINANCE}/ticker/bookTicker"):
        m = meta.get(t["symbol"])
        if m:
            d = t24.get(t["symbol"], {})
            out.append(("Binance", t["symbol"], m[0], m[1], float(t["bidPrice"]), float(t["askPrice"]), m[2],
                        float(d.get("quoteVolume") or 0), int(d.get("count") or 0)))
    return out


def mexc_rows():
    t24 = {t["symbol"]: t for t in _get(f"{MEXC}/ticker/24hr")}
    out = []
    for t in _get(f"{MEXC}/ticker/bookTicker"):
        sym = t["symbol"]
        for q in ("USDT", "USDC"):
            if sym.endswith(q) and sym[:-len(q)] in STABLES | FIATS and sym[:-len(q)] != q:
                d = t24.get(sym, {})
                out.append(("MEXC", sym, sym[:-len(q)], q, float(t["bidPrice"] or 0), float(t["askPrice"] or 0), None,
                            float(d.get("quoteVolume") or 0), int(d.get("count") or 0)))
    return out


def scan():
    rows, errors = [], {}
    for name, fn in (("Binance", binance_rows), ("MEXC", mexc_rows)):
        try:
            rows += fn()
        except Exception as e:                                          # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {str(e)[:100]}"
    return analyze(rows), errors


def to_markdown(res, errors, top=25):
    md = ["# Market making en pares estables y de moneda local — datos públicos", "",
          "Ganancia por vuelta = spread entre la mejor compra y la mejor venta, menos dos comisiones maker. "
          "Riesgo de precio bajo; el límite es la comisión real del par y la cola. Comisión supuesta: 0% "
          "(promoción) y 0,075% (Binance con BNB); la real se verifica con la clave de solo lectura.", "",
          "| exchange | par | spread % | tick % | neto 0% | neto 0,075% | volumen 24 h USD | operaciones/min |",
          "|---|---|---|---|---|---|---|---|"]
    for x in res[:top]:
        md.append(f"| {x['exchange']} | {x['pair']} | {x['spread_pct']} | {x['tick_pct']} | {x['net_pct'][0.0]:+.4f} | "
                  f"{x['net_pct'][0.075]:+.4f} | {x['vol_usd_24h']:,} | {x['trades_per_min']} |")
    if errors:
        md += ["", f"Errores: {errors}"]
    return "\n".join(md) + "\n"


if __name__ == "__main__":
    import json
    import sys
    res, err = scan()
    print(to_markdown(res, err))
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as f:
            json.dump({"pairs": res[:40], "errors": err}, f, default=str)
