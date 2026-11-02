"""Servidor RSS de prueba con ETag (cada fuente con su lista de entradas, la más nueva primero) y las
fuentes que lo leen."""
from collections import Counter
from datetime import UTC, datetime
from email.utils import format_datetime

from aiohttp import web

from tradingagent.ingestion.sources import SourceSpec


class Feeds:
    """Servidor RSS con ETag: cada fuente tiene su lista de entradas, la más nueva primero."""

    def __init__(self):
        self.entries: dict[str, list[tuple]] = {"media": [], "copia": [], "fed": []}
        self.version: Counter = Counter()
        self.requests: list[tuple[str, str | None]] = []
        self.down = False

    def add(self, name, guid, title, published, body=""):
        self.entries[name].insert(0, (guid, title, published, body))
        self.version[name] += 1

    async def handler(self, request):
        name = request.match_info["name"]
        self.requests.append((name, request.headers.get("If-None-Match")))
        if self.down:
            return web.Response(status=503)
        etag = f'"{name}-{self.version[name]}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304)
        items = "".join(
            f"<item><guid>{g}</guid><title>{t}</title><link>https://ex.com/{name}/{g}</link>"
            f"<pubDate>{format_datetime(datetime.fromtimestamp(p, UTC), usegmt=True)}</pubDate>"
            f"<description>{b}</description></item>" for g, t, p, b in self.entries[name])
        return web.Response(body=f"<rss><channel>{items}</channel></rss>".encode(), headers={"ETag": etag},
                            content_type="application/rss+xml")

    def app(self):
        a = web.Application()
        a.router.add_get("/{name}", self.handler)
        return a


def specs(base):
    return [SourceSpec("media", base + "/media", category="cripto"),
            SourceSpec("copia", base + "/copia", category="cripto"),
            SourceSpec("fed.monetary", base + "/fed", tier=1, category="macro", assets=("BTC", "ETH"))]
