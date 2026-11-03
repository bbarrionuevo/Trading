"""Recolector de datos de entrenamiento: activos, etiquetado con velas y corridas sucesivas con estado."""
import asyncio
import calendar
import contextlib
import copy
import json
import time

import aiohttp
import pytest
import yaml
from aiohttp import web

from conftest import serve
from fake_feeds import Feeds, specs
from fake_telegram import TOKEN, FakeTelegram
from tradingagent import config
from tradingagent.__main__ import main as cli
from tradingagent.alerts.telegram import TelegramTransport
from tradingagent.core.clock import ManualClock
from tradingagent.ingestion.catalog import DEFAULT_SOURCES
from tradingagent.research import assets
from tradingagent.research.collector import collect, read_shards
from tradingagent.research.labeler import MIN, CandleStore, basis, merge, minute, reaction

T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))              # lunes 2-nov-2026 12:00 UTC (minuto exacto)
BASES = {"BTCUSDT": 100.0, "ETHUSDT": 50.0, "SOLUSDT": 10.0}


# -- activos ----------------------------------------------------------------------------------------
def test_activos_en_orden_y_sin_falsos_positivos():
    assert assets.detect("Bitcoin ETF approved as $SOL and ETH rally") == ["BTC", "SOL", "ETH"]
    assert assets.detect("Here is a link to the near future of polkadot") == ["DOT"]     # 'link', 'near': palabras
    assert assets.detect("LINK and DOT surge while SOLANA stalls") == ["LINK", "DOT", "SOL"]
    assert assets.detect("ETHBTC ratio at lows") == []                                   # no son tickers sueltos
    assert assets.detect("BTC ETH SOL XRP DOGE") == ["BTC", "ETH", "SOL"]               # máximo 3


def test_pares_a_medir_siempre_con_btc_de_referencia():
    assert assets.symbols_for(["SOL"]) == ["SOLUSDT", "BTCUSDT"]
    assert assets.symbols_for([], ("BTC", "ETH")) == ["BTCUSDT", "ETHUSDT"]
    assert assets.symbols_for(["ETH", "SOL", "XRP", "DOGE", "ADA"]) == ["ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
    assert assets.symbols_for(["NOEXISTE"], ("TAMPOCO",)) == ["BTCUSDT"]


# -- velas sintéticas --------------------------------------------------------------------------------
def make_price(jumps: dict[str, tuple[int, float]]):
    """Precio de apertura por minuto: constante, con un salto opcional por par (minuto, factor)."""
    def price(sym: str, m: int) -> float:
        at, factor = jumps.get(sym, (None, 1.0))
        return BASES[sym] * (factor if at is not None and m >= at else 1.0)
    return price


def candles_for(price, sym: str, start: int, end: int) -> dict:
    out, m = {}, start
    while m <= end:
        o, c = price(sym, m), price(sym, m + MIN)
        out[m] = (o, max(o, c) * 1.001, min(o, c) * 0.999, c)
        m += MIN
    return out


def test_reaccion_desde_la_publicacion_y_lo_que_quedaba_al_recibirla():
    t_pub = T0 + 10 * 60 + 30                                  # publicada 12:10:30, recibida 12:15:10
    t_recv = T0 + 15 * 60 + 10
    m0 = minute(t_pub)
    price = make_price({"SOLUSDT": (m0 + 3 * MIN, 1.05)})      # +5 % a los 3 minutos de publicada
    c = candles_for(price, "SOLUSDT", m0 - 10 * MIN, minute(t_recv) + 60 * MIN)
    r = reaction(c, t_pub, t_recv)
    assert r["p0"] == 10.0 and r["pre_10m"] == 0.0
    assert (r["r_1m"], r["r_5m"], r["r_15m"], r["r_60m"]) == (0.0, 0.05, 0.05, 0.05)
    assert r["mfe_60m"] == pytest.approx(0.05105) and r["mae_60m"] == pytest.approx(-0.001)
    assert r["r_recv"] == 0.05 and r["r_recv_15m"] == 0.0     # al llegar, el movimiento ya había pasado
    hole = {k: v for k, v in c.items() if not m0 + 5 * MIN <= k < m0 + 20 * MIN}
    assert reaction(hole, t_pub, t_recv) is None                # hueco de 15 velas: la hora no es comparable
    assert reaction({k: v for k, v in c.items() if k != m0}, t_pub, t_recv) is None


def test_base_de_tiempo_publicacion_creible_o_llegada():
    assert basis({"received_at": T0, "published_at": T0 - 600}) == (T0 - 600, "publicada")
    assert basis({"received_at": T0, "published_at": T0 - 4 * 3600}) == (T0, "recibida")    # demasiado vieja
    assert basis({"received_at": T0, "published_at": T0 + 60}) == (T0, "recibida")          # en el futuro
    assert basis({"received_at": T0, "published_at": None}) == (T0, "recibida")
    assert merge([(0, 10 * MIN), (5 * MIN, 20 * MIN), (200 * MIN, 210 * MIN)]) == [(0, 20 * MIN), (200 * MIN, 210 * MIN)]
    assert merge([(0, MIN), (50 * MIN, 60 * MIN)]) == [(0, 60 * MIN)]                         # cerca: un solo pedido


# -- servidores de prueba ---------------------------------------------------------------------------
class Market:
    """Imita /api/v3/klines de Binance y /products/{p}/candles de Coinbase con el mismo precio."""

    def __init__(self, price):
        self.price = price
        self.binance_requests: list[tuple] = []
        self.coinbase_requests: list[tuple] = []
        self.binance_down: set[str] = set()                    # pares para los que Binance falla
        self.coinbase_down = False

    async def klines(self, request):
        q = request.query
        sym, start, end, limit = q["symbol"], int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        self.binance_requests.append((sym, start, end, limit))
        if sym in self.binance_down or "*" in self.binance_down:
            return web.json_response({"code": 0, "msg": "Service unavailable from a restricted location"}, status=451)
        if sym not in BASES:
            return web.json_response({"code": -1121, "msg": "Invalid symbol."}, status=400)
        rows = [[m, str(o), str(h), str(lo), str(c), "1", m + MIN - 1]
                for m, (o, h, lo, c) in sorted(candles_for(self.price, sym, start, end).items())][:limit]
        return web.json_response(rows)

    async def candles(self, request):
        product = request.match_info["product"]
        start, end = int(request.query["start"]), int(request.query["end"])
        self.coinbase_requests.append((product, start, end))
        sym = product.replace("-USD", "USDT")
        if self.coinbase_down or sym not in BASES:
            return web.json_response({"message": "NotFound"}, status=404)
        rows = [[m // 1000, lo, h, o, c, 1.0]
                for m, (o, h, lo, c) in sorted(candles_for(self.price, sym, start * 1000, end * 1000).items(),
                                               reverse=True)]
        return web.json_response(rows[:300])

    def app(self):
        a = web.Application()
        a.router.add_get("/api/v3/klines", self.klines)
        a.router.add_get("/products/{product}/candles", self.candles)
        return a

    @staticmethod
    def urls(base):
        return {"binance": base + "/api/v3/klines", "coinbase": base + "/products/{product}/candles"}


async def test_velas_por_rango_combinado_bloques_de_1000_y_respaldo_coinbase():
    price = make_price({})
    mk = Market(price)
    mk.binance_down = {"ETHUSDT"}
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        store = CandleStore(session, urls=Market.urls(url))
        await store.load({"BTCUSDT": [(T0 * 1000, T0 * 1000 + 1500 * MIN)],
                          "ETHUSDT": [(T0 * 1000, T0 * 1000 + 90 * MIN)], "XYZUSDT": [(T0 * 1000, T0 * 1000 + 5 * MIN)]})
    assert store.source == {"BTCUSDT": "binance", "ETHUSDT": "coinbase"}
    assert len(store.candles["BTCUSDT"]) == 1501
    btc = [r for r in mk.binance_requests if r[0] == "BTCUSDT"]
    assert len(btc) == 2 and all(r[3] <= 1000 and r[2] - r[1] <= 999 * MIN for r in btc)   # nunca más de 1000 velas
    assert len(store.candles["ETHUSDT"]) == 91
    assert "XYZUSDT" in store.errors and "HTTP 400" in store.errors["XYZUSDT"]


@pytest.fixture
def cfg():
    return copy.deepcopy(config.load(None))


async def test_corridas_sucesivas_historia_eventos_304_etiquetas_y_resumen(tmp_path, cfg):
    clock = ManualClock(T0)
    data = tmp_path / "datos"
    feeds, tg = Feeds(), FakeTelegram()
    for i in range(3):                                           # lo que ya estaba publicado: historia
        feeds.add("media", f"old{i}", f"Older market story number {i} about crypto", T0 - 3600 * (i + 1))
    feeds.add("fed", "old-fed", "Minutes of the Federal Open Market Committee", T0 - 86400)
    t_sol = T0 + 10 * 60
    price = make_price({"SOLUSDT": (minute(t_sol) + 3 * MIN, 1.05)})
    mk = Market(price)
    async with contextlib.AsyncExitStack() as stack, serve(feeds.app()) as furl, serve(mk.app()) as murl, \
            serve(tg.app()) as turl:
        t = TelegramTransport(TOKEN, "4242", base_url=turl)
        stack.push_async_callback(t.close)

        async def run():
            return await collect(data, cfg, {}, clock=clock, sources=specs(furl), telegram=t,
                                 candle_urls=Market.urls(murl), send_gap_s=0)

        # 1) primera corrida: todo es historia, no se guarda; avisa que está activo
        s1 = await run()
        assert s1["nuevas"] == 0 and s1["descartes"]["backlog"] == 4 and s1["fuentes_ok"] == 3
        assert not (data / "eventos").exists() and (data / "LEEME.md").exists()
        assert len(tg.messages) == 1 and "El recolector de noticias está andando" in tg.messages[0]["text"]

        # 2) noticias nuevas: original, copia de otra fuente, ruido, vieja y una oficial
        feeds.add("media", "sol", "Solana ETF approved by the SEC as SOL price jumps", t_sol,
                  body="The approval sends SOL higher.")
        feeds.add("copia", "sol2", "Solana ETF approved by the SEC as SOL price jumps", t_sol + 60)
        feeds.add("media", "pred", "Bitcoin price prediction: could BTC hit 200K this year", t_sol)
        feeds.add("media", "vieja", "A story from this morning about miners", T0 - 5 * 3600)
        feeds.add("fed", "fomc", "Federal Reserve issues FOMC statement", T0 + 12 * 60)
        clock.set(T0 + 15 * 60)
        s2 = await run()
        assert (s2["nuevas"], s2["copias"], s2["ruido"], s2["pendientes"]) == (3, 1, 1, 3)
        assert s2["descartes"].get("stale") == 1
        evs = read_shards(data, "eventos", T0, clock.time())
        by_guid = {e["url"].rsplit("/", 1)[1]: e for e in evs}
        assert set(by_guid) == {"sol", "sol2", "pred", "fomc"}
        sol, copy_ = by_guid["sol"], by_guid["sol2"]
        assert sol["dedup"] == "new" and sol["assets"] == ["SOL"] and sol["symbols"] == ["SOLUSDT", "BTCUSDT"]
        assert copy_["dedup"] == "exact" and copy_["cluster"] == sol["id"] and copy_["cluster_sources"] == 2
        assert by_guid["pred"]["is_noise"] and "prediccion_precio" in by_guid["pred"]["noise_reasons"]
        assert by_guid["fomc"]["symbols"] == ["BTCUSDT", "ETHUSDT"] and sol["age_s"] == 300.0
        alert = tg.messages[-1]
        assert "Reserva Federal (tasas de interés)" in alert["text"] and "FOMC" in alert["text"]
        assert alert["disable_notification"] is False
        assert len(tg.messages) == 2                              # el ruido y la copia no se avisan

        # 3) nada cambió: cada fuente responde 304 gracias al ETag guardado
        clock.set(T0 + 30 * 60)
        n_req = len(feeds.requests)
        s3 = await run()
        assert s3["nuevas"] == 0 and all(etag for _, etag in feeds.requests[n_req:])
        assert mk.binance_requests == []                          # todavía no pasaron 62 minutos

        # 4) a los 62 minutos de la llegada: etiquetas con un pedido por par
        clock.set(T0 + 15 * 60 + 63 * 60)
        s4 = await run()
        assert s4["etiquetadas"] == 3 and s4["pendientes"] == 0
        assert sorted(r[0] for r in mk.binance_requests) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        reac = read_shards(data, "reacciones", T0, clock.time())
        row = next(r for r in reac if r["event_id"] == sol["id"] and r["symbol"] == "SOLUSDT")
        assert row["t0_basis"] == "publicada" and row["t0"] == t_sol and row["lag_s"] == 300.0
        assert (row["r_1m"], row["r_15m"], row["src"]) == (0.0, 0.05, "binance")
        assert row["r_recv"] == 0.05                              # a los 5 min de publicada ya había saltado
        btc = next(r for r in reac if r["event_id"] == sol["id"] and r["symbol"] == "BTCUSDT")
        assert btc["r_15m"] == 0.0 and len(reac) == 5             # SOL+BTC, BTC+ETH (Fed) y BTC (predicción)

        # 5) primera corrida del día siguiente: resumen de ayer
        clock.set(T0 + 13 * 3600)                                 # 3-nov 01:00 UTC
        await run()
        dig = tg.messages[-1]["text"]
        assert "Resumen de noticias del 02/11" in dig and "SOL +5,0 % en 15 min" in dig
        assert "3 noticias" not in dig and "2 noticias" in dig    # el ruido no cuenta en el resumen
        state = json.loads((data / "estado.json").read_text(encoding="utf-8"))
        assert state["digest_day"] == "2026-11-03" and set(state["counts"]) == {"2026-11-02", "2026-11-03"}


async def test_sin_telegram_y_fuentes_caidas_avisa_una_sola_vez(tmp_path, cfg):
    clock = ManualClock(T0)
    feeds, tg = Feeds(), FakeTelegram()
    feeds.down = True
    async with contextlib.AsyncExitStack() as stack, serve(feeds.app()) as furl, \
            serve(Market(make_price({})).app()) as murl, serve(tg.app()) as turl:
        s = await collect(tmp_path / "sin", cfg, {}, clock=clock, sources=specs(furl), telegram=None,
                          candle_urls=Market.urls(murl), send_gap_s=0)
        assert s["fuentes_ok"] == 0 and s["telegram"] == 0           # sin Telegram los datos se juntan igual
        t = TelegramTransport(TOKEN, "4242", base_url=turl)
        stack.push_async_callback(t.close)
        data = tmp_path / "datos"
        for _ in range(3):
            clock.advance(900)
            await collect(data, cfg, {}, clock=clock, sources=specs(furl), telegram=t,
                          candle_urls=Market.urls(murl), send_gap_s=0)
        texts = [m["text"] for m in tg.messages]
        assert sum("No pude leer ninguna fuente de noticias" in x for x in texts) == 1
        feeds.down = False
        clock.advance(900)
        await collect(data, cfg, {}, clock=clock, sources=specs(furl), telegram=t,
                      candle_urls=Market.urls(murl), send_gap_s=0)
        assert "volvieron a responder" in tg.messages[-1]["text"]
        state = json.loads((data / "estado.json").read_text(encoding="utf-8"))
        assert all(h["ok"] for h in state["health"].values()) and "alerta_fuentes" not in state


async def test_velas_caidas_quedan_pendientes_y_se_reintentan(tmp_path, cfg):
    clock = ManualClock(T0)
    feeds, tg = Feeds(), FakeTelegram()
    mk = Market(make_price({}))
    mk.binance_down, mk.coinbase_down = {"*"}, True
    data = tmp_path / "datos"
    async with contextlib.AsyncExitStack() as stack, serve(feeds.app()) as furl, serve(mk.app()) as murl, \
            serve(tg.app()) as turl:
        t = TelegramTransport(TOKEN, "4242", base_url=turl)
        stack.push_async_callback(t.close)

        async def run():
            return await collect(data, cfg, {}, clock=clock, sources=specs(furl), telegram=t,
                                 candle_urls=Market.urls(murl), send_gap_s=0)

        await run()
        feeds.add("media", "eth", "Ethereum upgrade goes live on mainnet today", T0 + 5 * 60)
        clock.set(T0 + 10 * 60)
        await run()
        clock.set(T0 + 80 * 60)
        s = await run()
        assert s["etiquetadas"] == 0 and s["pendientes"] == 1 and s["descartes"]["etiqueta_reintento"] == 1
        assert sum("No pude bajar los precios" in m["text"] for m in tg.messages) == 1
        clock.advance(900)
        await run()
        assert sum("No pude bajar los precios" in m["text"] for m in tg.messages) == 1   # una sola vez
        mk.binance_down = set()                                   # vuelve Binance: se etiqueta
        clock.advance(900)
        s = await run()
        assert s["etiquetadas"] == 1 and s["pendientes"] == 0
        state = json.loads((data / "estado.json").read_text(encoding="utf-8"))
        assert "alerta_velas" not in state


async def test_pendientes_vencen_a_las_48_horas(tmp_path, cfg):
    clock = ManualClock(T0)
    feeds = Feeds()
    mk = Market(make_price({}))
    mk.binance_down, mk.coinbase_down = {"*"}, True
    data = tmp_path / "datos"
    async with serve(feeds.app()) as furl, serve(mk.app()) as murl:
        async def run():
            return await collect(data, cfg, {}, clock=clock, sources=specs(furl), telegram=None,
                                 candle_urls=Market.urls(murl), send_gap_s=0)

        await run()
        feeds.add("media", "x", "Exchange outage halts withdrawals for hours", T0 + 60)
        clock.set(T0 + 120)
        await run()
        clock.set(T0 + 49 * 3600)
        s = await run()
        assert s["pendientes"] == 0 and s["descartes"]["etiqueta_vencida"] == 1


# -- CLI ----------------------------------------------------------------------------------------------
async def test_cli_collect_imprime_solo_conteos_y_avisa_por_telegram(tmp_path, monkeypatch, capsys):
    feeds, tg = Feeds(), FakeTelegram()
    feeds.add("media", "a", "Some market story to start the history", time.time() - 60)   # el CLI usa el reloj real
    async with serve(feeds.app()) as furl, serve(tg.app()) as turl:
        conf = tmp_path / "config.yaml"                     # sin las fuentes reales: el test no sale a internet
        conf.write_text(yaml.safe_dump({"ingestion": {
            "sources": {s.id: {"enabled": False} for s in DEFAULT_SOURCES},
            "extra_sources": [{"id": "local", "url": furl + "/media", "category": "cripto"}]}}), encoding="utf-8")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
        monkeypatch.setenv("TELEGRAM_API_BASE", turl)
        assert await asyncio.to_thread(cli, ["collect", "--data", str(tmp_path / "d"), "--config", str(conf)]) == 0
        monkeypatch.delenv("TELEGRAM_CHAT_ID")
        assert await asyncio.to_thread(cli, ["collect", "--data", str(tmp_path / "d"), "--config", str(conf)]) == 0
    out, err = capsys.readouterr()
    first, second = (json.loads(x) for x in out.strip().splitlines())
    assert first["fuentes"] == 1 and first["descartes"] == {"backlog": 1} and first["telegram"] == 1
    assert second["telegram"] == 0 and "Telegram apagado" in err
    assert "El recolector de noticias está andando" in tg.messages[0]["text"] and TOKEN not in out + err


async def test_cli_avisar_manda_el_numero_por_telegram_y_no_lo_imprime(monkeypatch, capsys):
    tg = FakeTelegram()
    owner = {"id": 5150, "type": "private", "first_name": "Dueño"}
    tg.updates = [
        {"update_id": 1, "message": {"chat": owner, "text": "/id"}},
        {"update_id": 2, "message": {"chat": owner, "text": "listo"}},          # escribió algo después del /id
        {"update_id": 3, "my_chat_member": {"chat": owner}},                    # y un evento sin texto
        {"update_id": 4, "message": {"chat": {"id": 6160, "type": "private", "first_name": "Otro"}, "text": "hola"}},
        {"update_id": 5, "message": {"chat": {"id": -100777, "type": "group", "title": "Grupo"}, "text": "/id"}}]
    async with serve(tg.app()) as turl:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.setenv("TELEGRAM_API_BASE", turl)
        assert await asyncio.to_thread(cli, ["telegram-chats", "--avisar"]) == 0
        out1 = capsys.readouterr().out
        tg.updates = tg.updates[3:4]
        assert await asyncio.to_thread(cli, ["telegram-chats", "--avisar"]) == 1     # nadie pidió /id
        out2 = capsys.readouterr().out
        tg.updates = []
        assert await asyncio.to_thread(cli, ["telegram-chats", "--avisar"]) == 1     # otro programa se los llevó
        out3 = capsys.readouterr().out
    assert [m["chat_id"] for m in tg.messages] == ["5150"] and "5150" in tg.messages[0]["text"]
    assert "Bot @test_bot · Telegram devolvió 5 mensajes" in out1 and "3 chats, 2 privados, 1 con /id" in out1
    assert "Avisé su número a 1 de 1" in out1
    assert "Ningún chat privado le mandó /id a @test_bot" in out2
    assert "devolvió 0 mensajes" in out3 and "otro programa" in out3 and "@userinfobot" in out3
    for out in (out1, out2, out3):
        assert "5150" not in out and "6160" not in out and "777" not in out and TOKEN not in out


async def test_estudio_de_listados_mide_la_reaccion_en_mexc():
    import json as _json

    from fake_mexc import FakeMexc
    from tradingagent.research.history import listing_study

    t_ann = T0_STUDY = 1793700000
    clock = ManualClock(t_ann + 6 * 3600)
    mk = FakeMexc(lambda sym, m: 100.0 * (1.2 if m >= (t_ann + 300) * 1000 else 1.0), clock)
    arts = [{"title": "Binance Will List Foo (BTC) with Seed Tag Applied", "releaseDate": T0_STUDY * 1000},
            {"title": "Binance Futures Will Launch USDⓈ-Margined ETHUSDT Perpetual Contract", "releaseDate": T0_STUDY * 1000},
            {"title": "Binance Will Add Bar on Earn", "releaseDate": T0_STUDY * 1000}]

    async def page(request):
        page_no = int(request.query.get("pageNo", 1))
        body = {"code": "000000", "data": {"catalogs": [{"articles": arts if page_no == 1 else []}]}}
        return web.Response(text=_json.dumps(body), content_type="application/json")

    app = mk.app()
    app.router.add_get("/bapi", page)
    async with serve(app) as url, aiohttp.ClientSession() as s:
        rep = await listing_study(s, days=30, now=clock.time(), page_url=url + "/bapi?pageNo={page}", base=url)
    kinds = {e["tipo"]: e for e in rep["eventos"]}
    assert set(kinds) == {"listado_spot", "futuros"}                        # el de Earn no es un listado
    assert kinds["listado_spot"]["simbolo_mexc"] == "BTC_USDT"
    assert kinds["listado_spot"]["reaccion"]["r60"] > 0.15                  # subió 20 % después del anuncio
    assert rep["resumen"]["listado_spot"]["r60"]["positivos"] == 1


async def test_estudio_de_listados_de_bybit():
    import json as _json

    from fake_mexc import FakeMexc
    from tradingagent.research.history import listing_study

    t_ann = 1793700000
    clock = ManualClock(t_ann + 6 * 3600)
    mk = FakeMexc(lambda sym, m: 100.0 * (1.1 if m >= (t_ann + 300) * 1000 else 1.0), clock)
    ms = t_ann * 1000
    arts = [{"title": "Bybit to List KOR Protocol (BTC) on Spot", "publishTime": ms},          # formato real, 3-nov
            {"title": "BTC Token Splash: Grab a share of the prize pool", "publishTime": ms + 9},
            {"title": "Bybit to List KOR Protocol (BTC) on Spot", "publishTime": ms + 3600_000},  # repetido: el primero
            {"title": "New listing: Listing of ETHUSDT on Bybit Perpetual on Oct 16, 2026", "publishTime": ms},
            {"title": "New listing: AXISUSDT Perpetual Contract in Innovation Zone", "publishTime": ms},
            {"title": "New listing: Listing of PFEUSDT on Bybit TradFi Perpetual on Nov 3", "publishTime": ms},
            {"title": "Bybit to List Old (OLD) on Spot", "publishTime": (t_ann - 40 * 86400) * 1000}]   # fuera de fecha
    more = [{"title": "Bybit to  List GROVE (SOL) on Spot", "publishTime": ms - 86400_000},      # doble espacio, real
            {"title": "[Selected regions only] New listing: XRP/USDT | Grab a share of the prize", "publishTime": ms}]
    pages = {"1": arts, "2": more}                                  # la vieja de la página 1 no corta la búsqueda

    async def page(request):
        body = {"retCode": 0, "result": {"list": pages.get(request.query.get("page"), [])}}
        return web.Response(text=_json.dumps(body), content_type="application/json")

    app = mk.app()
    app.router.add_get("/bybit", page)
    async with serve(app) as url, aiohttp.ClientSession() as s:
        rep = await listing_study(s, days=30, now=clock.time(), page_url=url + "/bybit?page={page}", base=url,
                                  exchange="bybit")
    got = sorted((e["tipo"], e["ticker"]) for e in rep["eventos"])
    assert got == [("futuros", "AXIS"), ("futuros", "ETH"), ("listado_spot", "BTC"), ("listado_spot", "SOL"),
                   ("listado_spot", "XRP")]                                                 # sin TradFi ni promos
    spot = next(e for e in rep["eventos"] if e["ticker"] == "BTC")
    assert spot["t"] == t_ann and spot["reaccion"]["r60"] > 0.05 and rep["exchange"] == "bybit"
