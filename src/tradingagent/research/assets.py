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


def detect(text: str, limit: int = 3) -> list[str]:
    """Activos mencionados, en orden de primera aparición (máximo `limit`)."""
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
    return [a for _, a in sorted(found)][:limit]


def symbols_for(assets, hint=()) -> list[str]:
    """Pares a medir: los activos detectados (o los que sugiere la fuente) más BTC como referencia."""
    out = list(dict.fromkeys([*(a for a in assets if a in ASSETS), *(a for a in hint if a in ASSETS)]))
    if MARKET_PROXY not in out:
        out.append(MARKET_PROXY)
    return [f"{a}{QUOTE}" for a in out[:4]]
