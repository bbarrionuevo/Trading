"""Reproducción de noticias grabadas (JSONL) sobre un reloj: para probar la tubería completa con días
reales y para el harness de paper. Cada línea: {"title", "published_at" (ISO o época), "source",
"tier", "url", "body", "delay_s" (demora de llegada; por defecto 5 s)}."""
import json
from pathlib import Path

from ..core.events import TIER_MEDIA, NewsItem, stable_id
from .normalize import canonical_url, clean_text, parse_datetime


def _epoch(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, int | float):
        return float(v)
    return parse_datetime(str(v))


def load(path) -> list[dict]:
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as e:
            raise ValueError(f"{path}:{n}: JSON inválido ({e})") from e
    return sorted(rows, key=lambda r: _epoch(r.get("published_at")) or 0.0)


def to_item(row: dict, received_at: float) -> NewsItem:
    source = str(row.get("source") or "replay")
    url = canonical_url(row.get("url") or "")
    title = clean_text(str(row.get("title") or ""), 500)
    return NewsItem(id=stable_id(source, str(row.get("id") or url or title)), source=source,
                    tier=int(row.get("tier", TIER_MEDIA)), kind="replay", title=title,
                    body=clean_text(str(row.get("body") or "")), url=url,
                    published_at=_epoch(row.get("published_at")), received_at=received_at,
                    assets_hint=tuple(row.get("assets") or ()), category_hint=str(row.get("category") or ""))


async def replay(rows: list[dict], engine, clock) -> list:
    """Avanza el reloj manual hasta la llegada de cada noticia y la pasa por el motor."""
    out = []
    for row in rows:
        pub = _epoch(row.get("published_at"))
        arrival = (pub if pub is not None else clock.time()) + float(row.get("delay_s", 5.0))
        if arrival > clock.time():
            clock.set(arrival)
        out.append(await engine.handle(to_item(row, clock.time())))
    return out
