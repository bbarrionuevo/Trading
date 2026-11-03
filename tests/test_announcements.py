"""Anuncios oficiales de los exchanges (JSON): lectores, ingesta por la misma tubería que un RSS y un listado
nuevo que se reconoce y se puede operar."""
import json

import aiohttp
from aiohttp import web

from conftest import serve
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import TIER_OFFICIAL
from tradingagent.ingestion import announcements as an
from tradingagent.ingestion.catalog import DEFAULT_SOURCES
from tradingagent.ingestion.normalize import parse_datetime
from tradingagent.ingestion.sources import PollingSource, SourceSpec
from tradingagent.intelligence.classifier import classify
from tradingagent.marketdata.universe import Entry
from tradingagent.research import assets

MS = 1793726000000                                           # 3-nov-2026 17:13:20 UTC

BINANCE = {"code": "000000", "data": {"catalogs": [{"catalogId": 48, "articles": [
    {"id": 1, "code": "abc123", "title": "Binance Will List Foocoin (FOO) with Seed Tag Applied", "releaseDate": MS},
    {"id": 2, "code": "def456", "title": "Notice on New Trading Pairs", "releaseDate": MS - 60_000}]}]}}
OKX = {"code": "0", "data": [{"details": [{"annType": "announcements-new-listings", "pTime": str(MS),
                                           "title": "OKX to list FOO/USDT for spot trading",
                                           "url": "https://www.okx.com/help/okx-to-list-foo"}], "totalPage": "1"}]}
BITGET = {"code": "00000", "data": [{"annId": "99", "annTitle": "Bitget Lists FOOUSDT", "cTime": str(MS),
                                     "annUrl": "https://www.bitget.com/support/articles/99"}]}


def test_lectores_de_binance_okx_y_bitget():
    b = an.parse_binance(json.dumps(BINANCE).encode())
    assert [e.guid for e in b] == ["binance:abc123", "binance:def456"]
    assert parse_datetime(b[0].published) == MS / 1000 and b[0].link.endswith("/abc123")
    o = an.parse_okx(json.dumps(OKX).encode())
    assert o[0].title.startswith("OKX to list FOO") and parse_datetime(o[0].published) == MS / 1000
    g = an.parse_bitget(json.dumps(BITGET).encode())
    assert g[0].guid == "bitget:99" and parse_datetime(g[0].published) == MS / 1000
    assert an.parse_binance(b'{"data": null}') == []                      # vacío: nada, sin romperse


def test_catalogo_trae_los_anuncios_oficiales_como_fuentes_de_nivel_1():
    ids = {s.id: s for s in DEFAULT_SOURCES}
    for sid, fmt in (("binance.listados", "binance"), ("okx.listados", "okx"), ("bitget.listados", "bitget")):
        assert ids[sid].format == fmt and ids[sid].tier == TIER_OFFICIAL and ids[sid].interval_s <= 30
    assert ids["binance.listados"].enabled and ids["binance.listados"].interval_s <= 10     # llegar primero
    assert not ids["okx.listados"].enabled and not ids["bitget.listados"].enabled          # sin efecto medido


async def test_un_listado_oficial_entra_por_la_misma_tuberia_y_se_reconoce_la_moneda():
    async def handler(request):
        return web.json_response(BINANCE)

    app = web.Application()
    app.router.add_get("/bapi", handler)
    clock = ManualClock(MS / 1000 + 5)
    async with serve(app) as url, aiohttp.ClientSession() as s:
        p = PollingSource(SourceSpec("binance.listados", f"{url}/bapi", tier=TIER_OFFICIAL, format="binance"), s, clock,
                          None)
        items, first = await p.poll_once()
    assert first and len(items) == 2
    it = items[0]
    assert it.kind == "api" and it.tier == TIER_OFFICIAL and it.published_at == MS / 1000
    assets.register([Entry("FOO", "FOO_USDT", "cripto", 5e6)])            # FOO cotiza en MEXC futuros
    sig = classify(it.title)
    assert sig.category == "listado" and sig.assets == ("FOO",) and sig.polarity > 0
    assert classify("OKX to list FOO/USDT for spot trading").assets == ("FOO",)
    assert classify("Foo fighters will tour").assets != ("FOO",)          # la palabra suelta no es el ticker


def test_universo_con_monedas_chicas_solo_por_su_ticker_escrito():
    from tradingagent.marketdata import universe as uni_mod

    det = [{"symbol": s, "baseCoin": b, "baseCoinName": b, "quoteCoin": "USDT", "state": 0, "apiAllowed": True,
            "type": 1} for s, b in (("MARS_USDT", "MARS"), ("BIG_USDT", "BIG"), ("DUST_USDT", "DUST"))]
    tick = [{"symbol": "MARS_USDT", "amount24": 300_000}, {"symbol": "BIG_USDT", "amount24": 5e6},
            {"symbol": "DUST_USDT", "amount24": 10_000}]
    u = uni_mod.build(det, tick)
    assert u.keys() == {"MARS", "BIG"} and u.liquid_keys() == {"BIG"}      # la casi sin volumen no entra
    assets.register(u.by_key.values())
    assert assets.liquid_keys().count("MARS") == 0 and "BIG" in assets.liquid_keys()
    assert classify("Binance Will List Mars Token (MARS)").assets == ("MARS",)
    assert assets.detect("Mars mission launches") == []


async def test_prueba_de_red_informa_que_fuentes_se_leen_desde_la_pc():
    from tradingagent.research.probe import probe

    async def ok(request):
        return web.Response(text='{"data": [{"title": "Upbit lists FOO"}]}', content_type="application/json")

    async def blocked(request):
        return web.Response(status=403, text="<html>blocked</html>")

    app = web.Application()
    app.router.add_get("/ok", ok)
    app.router.add_get("/no", blocked)
    async with serve(app) as url, aiohttp.ClientSession() as s:
        rep = await probe(s, {"bien": f"{url}/ok", "bloqueada": f"{url}/no", "caida": "http://127.0.0.1:9/x"},
                          timeout_s=3, os_trust={"bien.windows": f"{url}/ok"})     # nunca sale a internet
    f = rep["fuentes"]
    assert f["bien.windows"]["codigo"] == 200                               # el intento con el almacén de Windows
    assert f["bien"]["codigo"] == 200 and "Upbit lists FOO" in f["bien"]["muestra"]
    assert f["bloqueada"]["codigo"] == 403 and f["caida"]["codigo"] is None and f["caida"]["error"]


UPBIT = {"success": True, "data": {"total_pages": 41, "total_count": 806, "notices": [
    {"listed_at": "2026-11-03T22:00:00+09:00", "first_listed_at": "2026-11-03T22:00:00+09:00", "id": 6730,
     "title": "푸코인(FOO) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", "category": "거래"},
    {"listed_at": "2026-10-30T16:00:00+09:00", "id": 6721, "title": "블러(BLUR) 거래지원 종료 안내 (11/30 15:00)",
     "category": "거래"}]}}


def test_avisos_de_upbit_en_coreano_listado_y_deslistado():
    """Formato verificado desde la PC del dueño (3-nov): desde la nube Upbit bloquea por país."""
    es = an.parse_upbit(json.dumps(UPBIT).encode())
    assert [e.guid for e in es] == ["upbit:6730", "upbit:6721"]
    assert parse_datetime(es[0].published) == parse_datetime("2026-11-03T13:00:00+00:00")
    assets.register([Entry("FOO", "FOO_USDT", "cripto", 5e6), Entry("BLUR", "BLUR_USDT", "cripto", 5e6)])
    new = classify(es[0].title)
    assert new.category == "listado" and new.polarity > 0 and new.assets == ("FOO",)   # "BTC 마켓" no es BTC
    gone = classify(es[1].title)
    assert gone.category == "deslistado" and gone.polarity < 0 and gone.assets == ("BLUR",)
    spec = {s.id: s for s in DEFAULT_SOURCES}["upbit.avisos"]
    assert spec.enabled and spec.os_trust and spec.format == "upbit" and spec.interval_s <= 10


def test_una_fuente_con_almacen_del_sistema_usa_ese_contexto_tls():
    import ssl

    spec = SourceSpec("x", "https://example.invalid/x", os_trust=True)
    p = PollingSource(spec, None, ManualClock(0), None)
    assert isinstance(p._ssl(), ssl.SSLContext)                            # truststore (almacén de Windows)
    assert PollingSource(SourceSpec("y", "https://example.invalid/y"), None, ManualClock(0), None)._ssl() is True
