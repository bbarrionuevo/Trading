"""Anuncios OFICIALES de los exchanges (APIs públicas en JSON), sobre todo listados nuevos: es la noticia que más
mueve un precio, y por estas APIs llega al segundo (los medios la publican minutos después).

Cada lector convierte la respuesta en `FeedEntry`, igual que un RSS, así el resto de la ingesta (deduplicado,
frescura, ruido, clasificación) es el mismo. Verificados el 3-nov-2026 contra las APIs reales: Binance (nuevos
listados, catálogo 48), OKX (nuevos listados) y Bitget (listados). Bybit y Upbit bloquean por país.
"""
import json
from datetime import UTC, datetime

from .feeds import FeedEntry, FeedError

BINANCE_LISTINGS = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=48"
                    "&pageNo=1&pageSize=20")
OKX_LISTINGS = "https://www.okx.com/api/v5/support/announcements?annType=announcements-new-listings"
BITGET_LISTINGS = "https://api.bitget.com/api/v2/public/annoucements?language=en_US&annType=coin_listings"


def _iso(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _load(body: bytes) -> dict:
    try:
        data = json.loads(body)
    except ValueError as e:
        raise FeedError(f"respuesta no JSON: {e}") from None
    if not isinstance(data, dict):
        raise FeedError("respuesta JSON inesperada")
    return data


def parse_binance(body: bytes) -> list[FeedEntry]:
    data = _load(body).get("data") or {}
    out = []
    for cat in data.get("catalogs") or []:
        for a in cat.get("articles") or []:
            if not a.get("title"):
                continue
            code = str(a.get("code") or a.get("id") or "")
            out.append(FeedEntry(title=str(a["title"]), link=f"https://www.binance.com/en/support/announcement/{code}",
                                 guid=f"binance:{code}", published=_iso(a.get("releaseDate"))))
    return out


def parse_okx(body: bytes) -> list[FeedEntry]:
    out = []
    for page in _load(body).get("data") or []:
        for a in page.get("details") or []:
            if not a.get("title"):
                continue
            out.append(FeedEntry(title=str(a["title"]), link=str(a.get("url") or ""),
                                 guid=f"okx:{a.get('url') or a['title']}", published=_iso(a.get("pTime"))))
    return out


def parse_bitget(body: bytes) -> list[FeedEntry]:
    out = []
    for a in _load(body).get("data") or []:
        if not a.get("annTitle"):
            continue
        out.append(FeedEntry(title=str(a["annTitle"]), link=str(a.get("annUrl") or ""),
                             guid=f"bitget:{a.get('annId') or a['annTitle']}", published=_iso(a.get("cTime"))))
    return out


PARSERS = {"binance": parse_binance, "okx": parse_okx, "bitget": parse_bitget}
