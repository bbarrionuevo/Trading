import asyncio
import json
import random

import aiohttp
from aiohttp import web

from conftest import serve
from tradingagent.core.clock import SystemClock
from tradingagent.ingestion.sources import PollingSource, SourceSpec
from tradingagent.ingestion.webhook import WebhookSource, sign

FEED = """<rss><channel>{items}</channel></rss>"""
ITEM = "<item><title>{t}</title><link>https://ex.com/{i}</link><guid>{i}</guid><pubDate>Thu, 01 Jan 2026 12:00:00 GMT</pubDate></item>"


def feed(n):
    return FEED.format(items="".join(ITEM.format(t=f"Headline number {i} about markets", i=i) for i in range(n)))


class Upstream:
    """Servidor RSS de prueba con comportamiento programable por pedido."""

    def __init__(self):
        self.script = []           # lista de (status, body, headers) consumida en orden
        self.requests = []

    async def handler(self, request):
        self.requests.append(dict(request.headers))
        status, body, headers = self.script.pop(0) if self.script else (200, feed(1), {})
        if status == "hang":
            await asyncio.sleep(1.5)                       # el cliente corta a los 0,5 s
        if request.headers.get("If-None-Match") == headers.get("ETag") and headers.get("ETag"):
            return web.Response(status=304)
        return web.Response(status=status, body=body.encode() if isinstance(body, str) else body, headers=headers)

    def app(self):
        a = web.Application()
        a.router.add_get("/feed", self.handler)
        return a


async def make_source(url, **kw):
    got = []

    async def on_items(spec, items, first):
        got.append((first, [i.title for i in items]))

    session = aiohttp.ClientSession()
    spec = SourceSpec("test", url + "/feed", interval_s=1, timeout_s=0.5, **kw)
    return PollingSource(spec, session, SystemClock(), on_items, rng=random.Random(1)), got, session


async def test_lectura_inicial_es_historia_luego_solo_nuevas_y_304():
    up = Upstream()
    async with serve(up.app()) as url:
        src, got, session = await make_source(url)
        async with session:
            up.script = [(200, feed(3), {"ETag": '"v1"'}), (200, feed(3), {"ETag": '"v1"'}),
                         (200, feed(4), {"ETag": '"v2"'})]
            await src.step()
            await src.step()                               # mismo ETag → 304, nada nuevo
            await src.step()
    assert got[0] == (True, ["Headline number 0 about markets", "Headline number 1 about markets",
                             "Headline number 2 about markets"])
    assert got[1] == (False, ["Headline number 3 about markets"])           # solo la nueva
    assert src.health.not_modified == 1 and up.requests[1].get("If-None-Match") == '"v1"'
    assert "tradingagent" in up.requests[0]["User-Agent"]


async def test_fallas_backoff_retry_after_y_circuito_abierto():
    up = Upstream()
    async with serve(up.app()) as url:
        src, got, session = await make_source(url, open_after=3, cooldown_s=120, max_backoff_s=60)
        async with session:
            up.script = [(500, "x", {}), (429, "x", {"Retry-After": "40"}), (404, "x", {})]
            d1 = await src.step()
            d2 = await src.step()
            d3 = await src.step()
            assert src.health.state == "abierta" and src.health.consecutive_failures == 3
            assert 1.7 <= d1 <= 2.3                             # 1 s × 2¹ ± jitter
            assert 34 <= d2 <= 46                               # respeta Retry-After (40 s)
            assert 100 <= d3 <= 140                             # circuito abierto: espera el enfriamiento
            up.script = [(200, feed(2), {})]
            d4 = await src.step()
            assert src.health.state == "ok" and src.health.consecutive_failures == 0 and d4 < 1.2
    assert "HTTP 500" in src.health.last_error or "HTTP 404" in src.health.last_error


async def test_timeout_y_respuesta_gigante_son_fallas_controladas():
    up = Upstream()
    async with serve(up.app()) as url:
        src, got, session = await make_source(url, max_bytes=1000)
        async with session:
            up.script = [("hang", "", {})]
            await src.step()
            assert "sin respuesta" in src.health.last_error
            up.script = [(200, "x" * 5000, {})]
            await src.step()
            assert "más de 1000 bytes" in src.health.last_error
            up.script = [(200, "<html>no</html>", {})]
            await src.step()
            assert "formato desconocido" in src.health.last_error
    assert got == []


async def test_webhook_firma_reloj_tamano_y_formato(clock):
    secret = "s" * 32
    received = []

    async def on_item(it):
        received.append(it)

    wh = WebhookSource(secret, on_item, clock, port=0, senders={"tree": 2}, max_body=2048)
    await wh.start()
    base = f"http://127.0.0.1:{wh.bound_port}/news"
    try:
        async with aiohttp.ClientSession() as s:
            async def post(payload, ts=None, secret_=secret, raw=None):
                body = raw if raw is not None else json.dumps(payload).encode()
                ts = str(int(clock.time() if ts is None else ts))
                h = {"X-TA-Timestamp": ts, "X-TA-Signature": sign(secret_, ts, body), "X-TA-Sender": "tree"}
                async with s.post(base, data=body, headers=h) as r:
                    return r.status

            assert await post({"title": "Exchange X hacked for $50M", "assets": ["eth"]}) == 202
            assert await post({"title": "x"}, secret_="otro-secreto-cualquiera") == 401
            assert await post({"title": "x"}, ts=clock.time() - 3600) == 401          # repetición vieja
            assert await post(None, raw=b"{no json") == 400
            assert await post({"title": "y" * 5000}) == 413
    finally:
        await wh.stop()
    assert len(received) == 1 and received[0].tier == 2 and received[0].assets_hint == ("ETH",)
    assert wh.counts["firma_invalida"] == 1 and wh.counts["reloj"] == 1 and wh.counts["malformados"] == 1
