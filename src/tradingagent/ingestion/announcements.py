"""Anuncios OFICIALES de los exchanges (APIs públicas en JSON), sobre todo listados nuevos: es la noticia que más
mueve un precio, y por estas APIs llega al segundo (los medios la publican minutos después).

Cada lector convierte la respuesta en `FeedEntry`, igual que un RSS, así el resto de la ingesta (deduplicado,
frescura, ruido, clasificación) es el mismo. Verificados el 3-nov-2026 contra las APIs reales: Binance (nuevos
listados, catálogo 48), OKX (nuevos listados), Bitget (listados) y Upbit (avisos de mercado, en coreano: desde la
PC y solo con el almacén de certificados de Windows, `SourceSpec.os_trust`).
"""
import json
from datetime import UTC, datetime

from .feeds import FeedEntry, FeedError

BINANCE_LISTINGS = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=48"
                    "&pageNo=1&pageSize=20")
# La lista SIN catálogo trae los 7 juntos (48, 49, 51, 93, 128, 157 y 161), con la misma forma; las dos direcciones
# dan la misma respuesta (7-nov, estudios/binance_catalogos_cache.py).
BINANCE_ALL = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
               "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query")
OKX_LISTINGS = "https://www.okx.com/api/v5/support/announcements?annType=announcements-new-listings"
BITGET_LISTINGS = "https://api.bitget.com/api/v2/public/annoucements?language=en_US&annType=coin_listings"
UPBIT_NOTICES = "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade"
# La lista de avisos de la web de trading de Bithumb: sin caché y a ~100 ms de Tokio (6 y 7-nov). Su API pública
# (api.bithumb.com/v1/notices → feed-api) pasa por Cloudflare con una copia de 4 h: no sirve.
BITHUMB_NOTICES = "https://gw.bithumb.com/feed/v2/customer-support/articles/notices/recent"


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


def parse_upbit(body: bytes) -> list[FeedEntry]:
    """Avisos de Upbit (en coreano): `data.notices[]` con `id`, `title`, `category` y `listed_at` (ISO, +09:00).
    Formato verificado desde la PC del dueño el 3-nov-2026 (desde la nube Upbit bloquea por país)."""
    out = []
    for a in (_load(body).get("data") or {}).get("notices") or []:
        if not a.get("title"):
            continue
        nid = str(a.get("id") or a.get("uuid") or a["title"])
        out.append(FeedEntry(title=str(a["title"]), link=f"https://upbit.com/service_center/notice?id={nid}",
                             guid=f"upbit:{nid}", published=str(a.get("listed_at") or a.get("first_listed_at") or "")))
    return out


def parse_bithumb(body: bytes) -> list[FeedEntry]:
    """Avisos de Bithumb (en coreano): `data[]` con `id`, `title`, `categoryName1` y `publicationDateTime` (hora de
    Corea, al segundo; los programados salen a la hora redonda). Son los últimos 5. Formato verificado desde Tokio el
    7-nov-2026. El enlace se arma con el número del aviso (no con el que trae la respuesta)."""
    data = _load(body).get("data")
    out = []
    for a in data if isinstance(data, list) else []:
        if not isinstance(a, dict) or not a.get("title") or not str(a.get("id") or "").isdigit():
            continue
        nid, when = str(a["id"]), str(a.get("publicationDateTime") or "").strip()
        out.append(FeedEntry(title=str(a["title"]), link=f"https://feed.bithumb.com/notice/{nid}",
                             guid=f"bithumb:{nid}", published=f"{when.replace(' ', 'T', 1)}+09:00" if when else "",
                             categories=(str(a["categoryName1"]),) if a.get("categoryName1") else ()))
    return out


PARSERS = {"binance": parse_binance, "okx": parse_okx, "bitget": parse_bitget, "upbit": parse_upbit,
           "bithumb": parse_bithumb}
