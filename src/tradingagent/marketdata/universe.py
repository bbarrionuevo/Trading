"""Universo operable en MEXC futuros: TODO lo que la noticia pueda mover (pedido del dueño, 3-nov: "operar con
distintas monedas, cripto, acciones o lo que sea necesario según la noticia").

MEXC tiene más de mil perpetuos USDT operables por API: criptos, acciones (Tesla, Nvidia, Apple...), índices
(S&P 500, Nasdaq), metales y petróleo. Este módulo arma el catálogo con la API pública (`contract/detail` y
`contract/ticker`):

- solo contratos activos, en USDT y con `apiAllowed` (los demás no se pueden operar por API);
- solo los líquidos: volumen de 24 h ≥ `MIN_ADV_USDT` (1 M USDT; un contrato sin volumen es una trampa: spread
  y deslizamiento comen cualquier ventaja, y antes de cada orden se revisan spread y profundidad);
- la clave de cada activo es como lo nombra la noticia: el ticker cripto (BTC), el de la acción (TSLA aunque
  el contrato sea TESLA_USDT) o el nombre del metal o el índice (XAU, SP500);
- nombres para reconocerlo en un texto: los de las empresas y materias primas que más noticias generan (para
  el resto vale el ticker escrito como ticker: `$FOO`, `(FOO)`, "FOO shares").

Se guarda en disco y se renueva cada `TTL_S` (la lista cambia con los listados nuevos).
"""
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import paths
from . import mexc

log = logging.getLogger("tradingagent.universe")
MIN_ADV_USDT = 1_000_000          # líquidos: se reconocen por nombre o ticker
# Monedas chicas (≥ 0,1 M USDT por día): entran al universo, pero su ticker solo cuenta escrito como ticker
# ("(MARSCOIN)", "$FOO"). Estudio del 3-nov (docs/ESTUDIO-LISTADOS.md): las que más subieron con un listado de
# Binance movían 0,1 a 0,9 M por día en MEXC. Antes de cada orden se revisan spread y profundidad igual.
MIN_ADV_SMALL_USDT = 100_000
TTL_S = 6 * 3600
CRYPTO, STOCK, INDEX, COMMODITY = "cripto", "accion", "indice", "materia_prima"
_TICKER = re.compile(r"^[A-Z0-9]{1,12}$")
_PAREN = re.compile(r"\(.*?\)")

# Nombres que identifican a un activo en un titular (en minúsculas, palabra entera). Lo más noticiable de la
# bolsa y las materias primas; las criptos usan research/assets.py y su ticker.
NAMES = {
    "TSLA": ("tesla",), "NVDA": ("nvidia",), "AAPL": ("apple",), "MSFT": ("microsoft",), "AMZN": ("amazon",),
    "GOOGL": ("google", "alphabet"), "META": ("meta platforms", "facebook"), "MSTR": ("microstrategy",),
    "CRCL": ("circle internet",), "INTC": ("intel",), "AMD": ("advanced micro devices",), "MU": ("micron",),
    "PLTR": ("palantir",), "ORCL": ("oracle corp", "oracle's"), "NFLX": ("netflix",), "AVGO": ("broadcom",), "DELL": ("dell",),
    "NKE": ("nike",), "MRNA": ("moderna",), "SNDK": ("sandisk",), "SAMSUNG": ("samsung",),
    "SKHYNIX": ("sk hynix",), "BABA": ("alibaba",), "JPM": ("jpmorgan",), "GME": ("gamestop",),
    "COIN": ("coinbase global",), "HOOD": ("robinhood",), "UBER": ("uber",), "DIS": ("disney",),
    "XAU": ("gold prices", "gold price", "spot gold", "gold futures"), "SILVER": ("silver prices", "spot silver"),
    "USOIL": ("crude oil", "wti", "oil prices"), "UKOIL": ("brent",), "SP500": ("s&p 500", "s&p500"),
    "NAS100": ("nasdaq 100", "nasdaq-100", "nasdaq composite"),
}
# No van nombres sueltos que en cripto significan otra cosa: "oracle" (los oráculos de precios), "gold"
# ("digital gold"), "nasdaq" (la bolsa donde cotiza una empresa) o "meta" a secas.
KIND_BY_KEY = {"XAU": COMMODITY, "XAUT": COMMODITY, "SILVER": COMMODITY, "USOIL": COMMODITY, "UKOIL": COMMODITY,
               "SP500": INDEX, "NAS100": INDEX, "SPY": INDEX, "QQQ": INDEX}


@dataclass(frozen=True)
class Entry:
    key: str                 # el activo como lo nombra la noticia (BTC, TSLA, XAU, SP500)
    symbol: str              # el contrato de MEXC (BTC_USDT, TESLA_USDT, XAU_USDT, SPX500_USDT)
    kind: str                # cripto | accion | indice | materia_prima
    adv: float               # volumen de 24 h en USDT
    names: tuple = ()        # nombres que lo identifican en un texto
    liquid: bool = True      # volumen ≥ MIN_ADV_USDT (las chicas solo se reconocen por su ticker escrito)


def _key(c: dict) -> str:
    """La clave de la noticia: el ticker de la acción (baseCoinName) o el del contrato (baseCoin)."""
    base = str(c.get("baseCoin") or "").upper()
    if int(c.get("type", 1) or 1) == 2:
        name = _PAREN.sub("", str(c.get("baseCoinName") or "")).strip().upper()
        if _TICKER.match(name) and name not in ("OIL",):
            return name
    return base


def _kind(c: dict, key: str) -> str:
    if key in KIND_BY_KEY:
        return KIND_BY_KEY[key]
    plates = " ".join(c.get("conceptPlate") or [])
    if "stockindex" in plates:
        return INDEX
    if "Stock" in plates or int(c.get("type", 1) or 1) == 2:
        return STOCK
    if "Commodities" in plates or "metals" in plates:
        return COMMODITY
    return CRYPTO


def build(details: list[dict], tickers: list[dict], min_adv: float = MIN_ADV_USDT,
          min_small: float = MIN_ADV_SMALL_USDT) -> "Universe":
    """Del catálogo y los tickers públicos de MEXC al universo operable (activo, por API, con volumen)."""
    adv = {t.get("symbol"): float(t.get("amount24") or 0) for t in tickers}
    entries: dict[str, Entry] = {}
    for c in details:
        sym = c.get("symbol", "")
        if c.get("quoteCoin") != "USDT" or c.get("state") != 0 or not c.get("apiAllowed") or c.get("isHidden"):
            continue
        if adv.get(sym, 0) < min(min_adv, min_small):
            continue
        key = _key(c)
        if not key:
            continue
        liquid = adv.get(sym, 0) >= min_adv
        e = Entry(key, sym, _kind(c, key), round(adv.get(sym, 0)), NAMES.get(key, ()) if liquid else (), liquid)
        if key not in entries or e.adv > entries[key].adv:          # dos contratos del mismo activo: el líquido
            entries[key] = e
    return Universe(entries.values())


class Universe:
    def __init__(self, entries=()):
        self.by_key = {e.key: e for e in entries}
        self.by_symbol = {e.symbol: e for e in entries}

    def __len__(self) -> int:
        return len(self.by_key)

    def symbol(self, key: str) -> str | None:
        e = self.by_key.get(str(key).upper())
        return e.symbol if e else None

    def key_of(self, symbol: str) -> str | None:
        e = self.by_symbol.get(symbol)
        return e.key if e else None

    def kind(self, key: str) -> str | None:
        e = self.by_key.get(str(key).upper())
        return e.kind if e else None

    def keys(self) -> set[str]:
        return set(self.by_key)

    def liquid_keys(self) -> set[str]:
        return {k for k, e in self.by_key.items() if e.liquid}

    def as_dict(self) -> dict:
        return {"entradas": [asdict(e) for e in self.by_key.values()]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Universe":
        out = []
        for x in (d or {}).get("entradas", []):
            try:
                out.append(Entry(x["key"], x["symbol"], x["kind"], float(x["adv"]), tuple(x.get("names") or ()),
                                 bool(x.get("liquid", True))))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(out)


async def load(session, cache: Path | None = None, *, base: str = mexc.BASE, now: float | None = None,
               min_adv: float = MIN_ADV_USDT) -> Universe:
    """El universo de la caché si tiene menos de `TTL_S`; si no, de la API pública (y se guarda). Sin red,
    la caché vieja; sin nada, vacío (se opera solo lo que ya conoce el programa)."""
    now = time.time() if now is None else now
    cached = paths.read_json(cache, None) if cache else None
    if isinstance(cached, dict) and now - float(cached.get("ts", 0)) < TTL_S:
        return Universe.from_dict(cached)
    try:
        details = await mexc._get(session, f"{base}/api/v1/contract/detail", None, "catálogo de contratos")
        tickers = await mexc._get(session, f"{base}/api/v1/contract/ticker", None, "tickers")
        uni = build(details if isinstance(details, list) else [details],
                    tickers if isinstance(tickers, list) else [tickers], min_adv)
    except Exception as e:                                           # noqa: BLE001 (sin catálogo se sigue igual)
        log.warning("no pude leer el catálogo de MEXC: %s", str(e)[:120])
        return Universe.from_dict(cached)
    if cache and len(uni):
        paths.write_json_atomic(cache, {"ts": round(now), **uni.as_dict()})
    return uni
