"""Canal oficial de anuncios de Binance por WebSocket (ingestion/binance_ws.py) contra un servidor falso: firma la
conexión con la clave de SOLO LECTURA, se suscribe, entrega cada anuncio a la ingesta como noticia oficial (y el
sondeo que trae el mismo título queda como duplicado), vuelve a conectar si se corta y nunca muestra la clave."""
import asyncio
import hashlib
import hmac
import json
import time

import aiohttp
from aiohttp import web

from conftest import serve
from tradingagent.core.bus import EventBus
from tradingagent.core.clock import SystemClock
from tradingagent.core.events import TIER_OFFICIAL, NewsItem
from tradingagent.ingestion.binance_ws import TOPIC, BinanceAnnouncements
from tradingagent.ingestion.dedup import DedupIndex
from tradingagent.ingestion.engine import IngestionEngine
from tradingagent.ingestion.noise import NoiseConfig, NoiseFilter

KEY, SECRET = "clave-de-lectura-de-prueba", "secreto-de-lectura-de-prueba"
LISTING = "Binance Will List Foo (FOO) with Seed Tag Applied"


class FakeAnnouncements:
    """El canal de Binance: verifica firma y clave, acepta SUBSCRIBE y empuja los anuncios de `queue`."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.connections = 0
        self.signed: list[bool] = []
        self.subscribed: list[str] = []
        self.drop_after_first = False

    async def time(self, request):
        return web.json_response({"serverTime": int(time.time() * 1000)})

    async def wss(self, request):
        raw = request.query_string.split("&signature=")[0]
        good = (request.headers.get("X-MBX-APIKEY") == KEY and request.query.get("topic") == TOPIC and
                request.query.get("signature") == hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
                and raw.startswith("random="))
        self.signed.append(good)
        if not good:
            return web.json_response({"code": -1022, "msg": "Signature for this request is not valid."}, status=400)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        await ws.send_json({"type": "COMMAND", "data": "SUCCESS", "subType": "REGISTER", "code": "00000000"})
        msg = await ws.receive_json()
        self.subscribed.append(msg.get("value"))
        await ws.send_json({"type": "COMMAND", "data": "SUCCESS", "subType": "SUBSCRIBE", "code": "00000000"})
        if self.drop_after_first and self.connections == 1:
            await ws.close()
            return ws
        while not ws.closed:
            try:
                a = await asyncio.wait_for(self.queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            await ws.send_json({"type": "DATA", "topic": TOPIC, "data": json.dumps(a)})
        return ws

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_get("/api/v3/time", self.time)
        a.router.add_get("/sapi/wss", self.wss)
        return a


def announcement(title=LISTING, catalog=48, name="New Cryptocurrency Listing", age_s=0.5):
    return {"catalogId": catalog, "catalogName": name, "publishDate": int((time.time() - age_s) * 1000), "title": title,
            "body": "Binance will list Foo (FOO) and open trading for FOO/USDT.", "disclaimer": "Trade on-the-go"}


async def _wait(cond, limit_s=5.0):
    end = time.monotonic() + limit_s
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("no pasó a tiempo")
        await asyncio.sleep(0.02)


async def test_firma_con_la_clave_de_lectura_y_entrega_cada_anuncio_como_oficial():
    fake = FakeAnnouncements()
    got: list[tuple[NewsItem, float]] = []

    async def on_item(item, max_age_s=None):
        got.append((item, max_age_s))

    stop = asyncio.Event()
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        src = BinanceAnnouncements(KEY, SECRET, on_item, SystemClock(), url=url.replace("http", "ws") + "/sapi/wss",
                                   time_url=f"{url}/api/v3/time")
        task = asyncio.create_task(src.run(stop, s))
        await _wait(lambda: src.connected)
        await fake.queue.put(announcement())
        await fake.queue.put(announcement("Notice of Removal of Spot Trading Pairs - 2026-11-07", 161, "Delisting"))
        await _wait(lambda: len(got) == 2)
        stop.set()
        await asyncio.wait_for(task, 10)
    assert fake.signed == [True] and fake.subscribed == [TOPIC]
    item, max_age = got[0]
    assert item.source == "binance.ws" and item.tier == TIER_OFFICIAL and item.title == LISTING
    assert item.category_hint == "listado" and got[1][0].category_hint == "deslistado" and max_age == 600.0
    assert 0 <= item.received_at - item.published_at < 5                     # llega cuando Binance publica
    assert src.counts["anuncios"] == 2 and src.health.state == "ok"


async def test_si_se_corta_vuelve_a_conectar_con_espera():
    fake = FakeAnnouncements()
    fake.drop_after_first = True
    got = []

    async def on_item(item, max_age_s=None):
        got.append(item)

    stop = asyncio.Event()
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        src = BinanceAnnouncements(KEY, SECRET, on_item, SystemClock(), url=url.replace("http", "ws") + "/sapi/wss",
                                   time_url=f"{url}/api/v3/time", backoff_s=(0.05,))
        task = asyncio.create_task(src.run(stop, s))
        await _wait(lambda: fake.connections >= 2 and src.connected)
        await fake.queue.put(announcement())
        await _wait(lambda: len(got) == 1)
        stop.set()
        await asyncio.wait_for(task, 10)
    assert src.counts["cortes"] >= 1 and src.counts["conexiones"] >= 2
    assert KEY not in json.dumps(src.snapshot()) and SECRET not in json.dumps(src.snapshot())


async def test_el_mismo_anuncio_por_el_canal_y_por_el_sondeo_se_opera_una_vez():
    """Lo que llegue primero gana; el otro queda como duplicado (el título es el mismo)."""
    bus = EventBus()
    clock = SystemClock()
    eng = IngestionEngine(bus, clock, DedupIndex(clock), NoiseFilter(NoiseConfig()), default_max_age_s=600)
    src = BinanceAnnouncements(KEY, SECRET, eng.handle, clock)
    ws_item = src.parse(json.dumps({"type": "DATA", "topic": TOPIC, "data": json.dumps(announcement())}))
    first = await eng.handle(ws_item, max_age_s=600)
    polled = NewsItem(id="binance.listados:1", source="binance.listados", tier=TIER_OFFICIAL, kind="api",
                      title=LISTING, url="https://www.binance.com/en/support/announcement/abc",
                      published_at=ws_item.published_at, received_at=clock.time() + 2)
    second = await eng.handle(polled, max_age_s=600)
    assert first.accepted and not second.accepted and second.dedup.kind != "new"
    assert src.parse(json.dumps({"type": "COMMAND", "data": "SUCCESS", "subType": "SUBSCRIBE"})) is None


async def test_un_catalogo_que_no_conocemos_se_avisa_una_vez_despues_de_entregar_el_anuncio():
    """Pedido del dueño (7-nov): si Binance publica en un catálogo nuevo, avisar (una vez por catálogo) y seguir."""
    seen = []
    src = BinanceAnnouncements(KEY, SECRET, None, SystemClock(), on_new_catalog=lambda *a: seen.append(a))

    def msg(**kw):
        return json.dumps({"type": "DATA", "topic": TOPIC, "data": json.dumps(announcement(**kw))})

    for cat in (48, 161, 49, 51, 93, 128, 157):                      # los 7 de la lista sin catálogo: conocidos
        assert src.parse(msg(catalog=cat)) is not None
    item = src.parse(msg(title="Binance Launches Something New", catalog=999, name="New Things"))
    assert item is not None and item.category_hint == "New Things"   # el anuncio sigue su camino igual
    assert seen == []                                                 # el aviso sale después (call_soon)
    await asyncio.sleep(0)
    assert seen == [(999, "New Things", "Binance Launches Something New")]
    src.parse(msg(title="Another one", catalog=999, name="New Things"))
    await asyncio.sleep(0)
    assert len(seen) == 1                                             # una sola vez por catálogo
