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
# (en la PC, el 3-nov, "Mercado Bitcoin", un exchange de Brasil, abrió un corto en BTC; "LTC Properties", una
# inmobiliaria, se tomó como Litecoin; ETH Zurich es una universidad).
_NOT_ASSET = re.compile(
    r"(?i)\b(mercado bitcoin|bitcoin (cash|sv|gold|depot|suisse|magazine|well|group)|bitcoin\.com|"
    r"ethereum (classic|name service|pow)|ltc properties|eth z(u|ü)rich)\b|"
    r"\([A-Z, ]*마켓\)|"                                  # Upbit: "(KRW, BTC, USDT 마켓)" son mercados, no la moneda
    # El país en el nombre legal de una empresa: "8-K - Jerash Holdings (US), Inc." (5-nov) se leyó como la cripto US.
    # Solo seguido de la forma societaria: en "Binance Will List Foo (US)" el paréntesis sí es el ticker.
    r"\((US|USA|UK|HK|BVI)\)(?=,?\s+(Inc|Corp|Corporation|Co|Ltd|Limited|LLC|LP|L\.P|plc|PLC|N\.V|S\.A)\b)|"
    r"/(BTC|ETH)\b")                       # BTC o ETH como cotización de un par: "KAIA/BTC" es KAIA ("FOO/USDT" sigue)


# El resto del universo de MEXC (acciones, índices, metales, petróleo y criptos fuera de las de arriba), que se
# carga en vivo (marketdata/universe.py). Para no inventar activos (C-1), su ticker solo cuenta escrito como
# ticker: "$FOO", "(FOO)", "FOO/USDT", "FOO shares/stock/token/coin", "NASDAQ: FOO"; o por un nombre conocido.
_EXTRA: set[str] = set()
_EXTRA_NAME_RE: dict[str, re.Pattern] = {}
_EXTRA_TICKER_RE: re.Pattern | None = None


def register(entries) -> int:
    """Suma al reconocimiento los activos del universo operable (los `Entry` de marketdata/universe.py)."""
    global _EXTRA_TICKER_RE
    extra = [e for e in entries if e.key not in ASSETS]
    _EXTRA.clear()
    _EXTRA.update(e.key for e in extra)
    _LIQUID.clear()
    _LIQUID.update(e.key for e in extra if getattr(e, "liquid", True))
    _EXTRA_NAME_RE.clear()
    for e in extra:
        if e.names:
            _EXTRA_NAME_RE[e.key] = re.compile(r"\b(" + "|".join(map(re.escape, e.names)) + r")\b", re.IGNORECASE)
    # Un ticker sin letras no se puede reconocer en un texto: la moneda "4" de MEXC aparecía en "$4.2B" o en
    # "EPS of -$4.26" (3-nov). Sigue en el universo (la IA puede nombrarla), pero el texto no la detecta.
    tickers = [k for k in _EXTRA if any(c.isalpha() for c in k)]
    if not tickers:
        _EXTRA_TICKER_RE = None
        return len(_EXTRA)
    alt = "|".join(map(re.escape, sorted(tickers, key=len, reverse=True)))
    _EXTRA_TICKER_RE = re.compile(
        rf"\$({alt})(?![A-Za-z0-9])|\(({alt})\)|(?<![A-Za-z0-9])({alt})(?=\s+(?:shares|stock|stocks|token|tokens|coin|"
        rf"futures)\b)|(?:NASDAQ|NYSE|Nasdaq|Nyse)\s*:\s*({alt})(?![A-Za-z0-9])|(?<![A-Za-z0-9])({alt})/USDT?\b")
    return len(_EXTRA)


def known(asset: str) -> bool:
    """¿Es un activo que el programa reconoce (los de siempre o el universo de MEXC)?"""
    return asset in ASSETS or asset in _EXTRA


_LIQUID: set[str] = set()


def all_keys() -> list[str]:
    return sorted(set(ASSETS) | _EXTRA)


def liquid_keys() -> list[str]:
    """Los de siempre más los líquidos del universo (los que van en la lista que ve la IA)."""
    return sorted(set(ASSETS) | _LIQUID)


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
