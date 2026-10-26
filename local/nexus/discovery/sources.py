"""Fuentes públicas (sin API key) de libros top-of-book."""
import requests

S = requests.Session()


def _get(url, **kw):
    r = S.get(url, timeout=(5, 10), **kw)
    r.raise_for_status()
    return r.json()


def kraken_book(quotes=("EUR", "USDC", "USD", "BTC", "ETH", "EURC", "USDT", "EURR", "EURQ", "USDQ", "USDG", "RLUSD")):
    """Todos los pares spot de Kraken cuyas dos monedas estén en un universo líquido."""
    pairs = _get("https://api.kraken.com/0/public/AssetPairs")["result"]
    tick = _get("https://api.kraken.com/0/public/Ticker")["result"]
    book = {}
    for k, p in pairs.items():
        if k not in tick or ".d" in k:
            continue
        base, quote = p.get("wsname", "/").split("/") if "wsname" in p else (None, None)
        if not base or quote not in quotes:
            continue
        base = {"XBT": "BTC", "XDG": "DOGE"}.get(base, base)
        quote = {"XBT": "BTC"}.get(quote, quote)
        t = tick[k]
        fee = float(p["fees"][0][1]) if p.get("fees") else 0.4
        bid, ask = float(t["b"][0]), float(t["a"][0])
        vol_quote = float(t["v"][1]) * float(t["p"][1])       # volumen 24h en moneda cotizada
        book[f"{base}/{quote}"] = (base, quote, bid, ask, fee, float(t["b"][2]) * bid,
                                   float(t["a"][2]) * ask, vol_quote)
    # Conversión oficial EUR<->EURC 1:1 sin comisión ni spread (límite 25k EUR/día por dirección)
    book["EURC/EUR(conv)"] = ("EURC", "EUR", 1.0, 1.0, 0.0, 25000.0, 25000.0, 1e9)
    return book


def bitstamp_book(pairs=("btceur", "etheur", "usdceur", "eurceur", "xrpeur", "soleur")):
    out = {}
    for p in pairs:
        try:
            t = _get(f"https://www.bitstamp.net/api/v2/ticker/{p}/")
            out[p[:-3].upper() + "/EUR"] = (float(t["bid"]), float(t["ask"]))
        except Exception:
            pass
    return out


def coinbase_book(pairs=("BTC-EUR", "ETH-EUR", "USDC-EUR", "SOL-EUR", "XRP-EUR")):
    out = {}
    for p in pairs:
        try:
            t = _get(f"https://api.exchange.coinbase.com/products/{p}/ticker")
            out[p.replace("-", "/")] = (float(t["bid"]), float(t["ask"]))
        except Exception:
            pass
    return out


def criptoya(coin, fiat="ARS", vol=100):
    return _get(f"https://criptoya.com/api/{coin}/{fiat}/{vol}")


BINANCE_DATA = "https://data-api.binance.vision"


def binance_book(quotes=("USDT", "USDC", "BTC", "ETH", "BNB", "FDUSD", "EUR", "ARS", "TRY", "BRL"),
                 fee=0.075):
    """Top-of-book de todos los pares spot de Binance (endpoint público de datos de mercado)."""
    info = _get(f"{BINANCE_DATA}/api/v3/exchangeInfo?permissions=SPOT")["symbols"]
    meta = {s["symbol"]: (s["baseAsset"], s["quoteAsset"]) for s in info
            if s["status"] == "TRADING" and s["quoteAsset"] in quotes}
    book = {}
    for t in _get(f"{BINANCE_DATA}/api/v3/ticker/bookTicker"):
        m = meta.get(t["symbol"])
        if m and float(t["bidPrice"]) > 0 and float(t["askPrice"]) > 0:
            bid, ask = float(t["bidPrice"]), float(t["askPrice"])
            book[f"{m[0]}/{m[1]}"] = (m[0], m[1], bid, ask, fee, float(t["bidQty"]) * bid,
                                     float(t["askQty"]) * ask, None)
    return book


def binance_klines(symbol="BTCUSDT", interval="1h", limit=1000, end=None):
    q = f"{BINANCE_DATA}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    if end:
        q += f"&endTime={int(end)}"
    return [(k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in _get(q)]


def binance_24h(quote_eur):
    """[(par, bid, ask, vol24h_eur)] para todos los pares; quote_eur: {moneda: EUR por unidad}."""
    out = []
    for t in _get(f"{BINANCE_DATA}/api/v3/ticker/24hr"):
        sym = t["symbol"]
        for q, rate in quote_eur.items():
            if sym.endswith(q):
                out.append((sym[:-len(q)] + "/" + q, float(t["bidPrice"]), float(t["askPrice"]),
                            float(t["quoteVolume"]) * rate))
                break
    return out


def mexc_book_all():
    """Top-of-book de todos los pares spot de MEXC: {simbolo: (bid, ask, bid_qty, ask_qty)}."""
    out = {}
    for t in _get("https://api.mexc.com/api/v3/ticker/bookTicker"):
        try:
            out[t["symbol"]] = (float(t["bidPrice"]), float(t["askPrice"]), float(t["bidQty"]), float(t["askQty"]))
        except (TypeError, ValueError, KeyError):
            pass
    return out


def binance_book_all():
    out = {}
    for t in _get(f"{BINANCE_DATA}/api/v3/ticker/bookTicker"):
        try:
            out[t["symbol"]] = (float(t["bidPrice"]), float(t["askPrice"]), float(t["bidQty"]), float(t["askQty"]))
        except (TypeError, ValueError, KeyError):
            pass
    return out


def mexc_funding():
    """[(exchange, simbolo, funding_por_periodo, periodos_por_dia, volumen_24h_usdt)] de perpetuos MEXC.
    Se asume funding cada 8 h (3 por día), que es lo habitual; algunos pares usan otro intervalo."""
    rows = []
    for t in _get("https://contract.mexc.com/api/v1/contract/ticker").get("data", []):
        try:
            rows.append(("MEXC", t["symbol"], float(t["fundingRate"]), 3, float(t.get("amount24") or 0)))
        except (TypeError, ValueError, KeyError):
            pass
    return rows


def gate_funding():
    rows = []
    for t in _get("https://api.gateio.ws/api/v4/futures/usdt/tickers"):
        try:
            rows.append(("Gate", t["contract"], float(t["funding_rate"]), 3, float(t.get("volume_24h_quote") or 0)))
        except (TypeError, ValueError, KeyError):
            pass
    return rows


def gate_spot_pairs():
    """Pares spot de Gate con precio (para saber si el carry se puede armar en el mismo exchange)."""
    return {t["currency_pair"] for t in _get("https://api.gateio.ws/api/v4/spot/tickers")
            if float(t.get("last") or 0) > 0}
