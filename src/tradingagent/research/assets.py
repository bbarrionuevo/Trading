"""Detección de activos mencionados en una noticia (versión determinista; el NLP de la etapa 2 la mejora).

Reglas para no inventar activos (REGLAS C-1):
- los nombres ("bitcoin", "solana") se buscan sin importar mayúsculas, como palabra entera;
- los tickers que también son palabras comunes en inglés (LINK, DOT, NEAR, TON, SOL, OP, ADA...) solo
  cuentan en MAYÚSCULAS o con $ adelante ("$SOL"): "a link to" no es Chainlink.
"""
import re

# activo: (nombres sin importar mayúsculas, tickers que solo valen en mayúsculas)
ASSETS = {
    "BTC": (("bitcoin",), ("BTC", "XBT")),
    "ETH": (("ethereum", "ether"), ("ETH",)),
    "SOL": (("solana",), ("SOL",)),
    "XRP": (("ripple",), ("XRP",)),
    "BNB": (("binance coin",), ("BNB",)),
    "DOGE": (("dogecoin",), ("DOGE",)),
    "ADA": (("cardano",), ("ADA",)),
    "AVAX": (("avalanche",), ("AVAX",)),
    "LINK": (("chainlink",), ("LINK",)),
    "LTC": (("litecoin",), ("LTC",)),
    "DOT": (("polkadot",), ("DOT",)),
    "TON": (("toncoin",), ("TON",)),
    "TRX": (("tron",), ("TRX",)),
    "SUI": ((), ("SUI",)),
    "ARB": (("arbitrum",), ("ARB",)),
    "OP": ((), ("OP",)),
    "NEAR": (("near protocol",), ("NEAR",)),
    "APT": (("aptos",), ("APT",)),
    "SHIB": (("shiba inu",), ("SHIB",)),
    "PEPE": ((), ("PEPE",)),
}
QUOTE = "USDT"
MARKET_PROXY = "BTC"                         # reacción del mercado en general

_NAME_RE = {a: re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.IGNORECASE)
            for a, (names, _) in ASSETS.items() if names}
_TICKER_RE = {a: re.compile(r"(?<![A-Za-z0-9])\$?(" + "|".join(tickers) + r")(?![A-Za-z0-9])")
              for a, (_, tickers) in ASSETS.items()}


# Nombres de empresas, productos u otras monedas que contienen el nombre de un activo y NO son ese activo
# (en la PC, el 3-nov, "Mercado Bitcoin", un exchange de Brasil, abrió un corto en BTC).
_NOT_ASSET = re.compile(
    r"(?i)\b(mercado bitcoin|bitcoin (cash|sv|gold|depot|suisse|magazine|well|group)|bitcoin\.com|"
    r"ethereum (classic|name service|pow))\b")


# El resto del universo de MEXC (acciones, índices, metales, petróleo y criptos fuera de las de arriba), que se
# carga en vivo (marketdata/universe.py). Para no inventar activos (C-1), su ticker solo cuenta escrito como
# ticker: "$FOO", "(FOO)", "FOO shares/stock/token/coin", "NASDAQ: FOO"; o por un nombre conocido ("Tesla").
_EXTRA: set[str] = set()
_EXTRA_NAME_RE: dict[str, re.Pattern] = {}
_EXTRA_TICKER_RE: re.Pattern | None = None


def register(entries) -> int:
    """Suma al reconocimiento los activos del universo operable (los `Entry` de marketdata/universe.py)."""
    global _EXTRA_TICKER_RE
    extra = [e for e in entries if e.key not in ASSETS]
    _EXTRA.clear()
    _EXTRA.update(e.key for e in extra)
    _EXTRA_NAME_RE.clear()
    for e in extra:
        if e.names:
            _EXTRA_NAME_RE[e.key] = re.compile(r"\b(" + "|".join(map(re.escape, e.names)) + r")\b", re.IGNORECASE)
    if not _EXTRA:
        _EXTRA_TICKER_RE = None
        return 0
    alt = "|".join(map(re.escape, sorted(_EXTRA, key=len, reverse=True)))
    _EXTRA_TICKER_RE = re.compile(
        rf"\$({alt})(?![A-Za-z0-9])|\(({alt})\)|(?<![A-Za-z0-9])({alt})(?=\s+(?:shares|stock|stocks|token|tokens|coin|"
        rf"futures)\b)|(?:NASDAQ|NYSE|Nasdaq|Nyse)\s*:\s*({alt})(?![A-Za-z0-9])")
    return len(_EXTRA)


def known(asset: str) -> bool:
    """¿Es un activo que el programa reconoce (los de siempre o el universo de MEXC)?"""
    return asset in ASSETS or asset in _EXTRA


def all_keys() -> list[str]:
    return sorted(set(ASSETS) | _EXTRA)


def detect(text: str, limit: int = 3) -> list[str]:
    """Activos mencionados, en orden de primera aparición (máximo `limit`)."""
    text = _NOT_ASSET.sub(lambda m: " " * len(m.group(0)), text)
    found = []
    for a in ASSETS:
        pos = []
        m = _NAME_RE[a].search(text) if a in _NAME_RE else None
        if m:
            pos.append(m.start())
        t = _TICKER_RE[a].search(text)
        if t:
            pos.append(t.start())
        if pos:
            found.append((min(pos), a))
    first: dict[str, int] = {}
    for a, rx in _EXTRA_NAME_RE.items():
        m = rx.search(text)
        if m:
            first[a] = m.start()
    if _EXTRA_TICKER_RE is not None:
        for m in _EXTRA_TICKER_RE.finditer(text):
            a = next(g for g in m.groups() if g)
            first[a] = min(first.get(a, m.start()), m.start())
    found += [(pos, a) for a, pos in first.items()]
    return [a for _, a in sorted(found)][:limit]


def symbols_for(assets, hint=()) -> list[str]:
    """Pares a medir: los activos detectados (o los que sugiere la fuente) más BTC como referencia."""
    out = list(dict.fromkeys([*(a for a in assets if a in ASSETS), *(a for a in hint if a in ASSETS)]))
    if MARKET_PROXY not in out:
        out.append(MARKET_PROXY)
    return [f"{a}{QUOTE}" for a in out[:4]]
