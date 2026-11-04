"""Operador paper EN VIVO (live.py) contra un MEXC falso: reacciona a cada noticia al instante, vigila las
barreras con el libro, sobrevive a un reinicio, respeta KILL y consulta a Claude solo lo dudoso."""
import calendar
import copy
import json
from decimal import Decimal as D

import aiohttp
import pytest

from conftest import serve
from fake_anthropic import KEY, FakeClaude
from fake_mexc import FakeMexc
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.intelligence.classifier import classify
from tradingagent.intelligence.llm import LlmClassifier
from tradingagent.live import LiveTrader
from tradingagent.research.futures import MexcFutures

T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))
HACK = "Binance hacked: $40M in BTC stolen"


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)

    def texts(self):
        return [a.text for a in self.items]


def news(nid, title, now, lag=10):
    it = NewsItem(id=nid, source="media", tier=2, kind="rss", title=title, published_at=now - lag, received_at=now - 1)
    return IngestedNews(item=it, age_s=lag, freshness="fresh", dedup=DedupResult("new", nid), noise=NoiseResult(0, False),
                        accepted=True)


async def make(session, url, clock, home, alerts, llm=None):
    t = LiveTrader(MexcFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=alerts, home=home,
                   capital=120.0, llm=llm, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)))
    await t.start()
    return t


def state(home):
    return json.loads((home / "paper-vivo" / "mexc-futuros" / "estado.json").read_text(encoding="utf-8"))


async def test_reacciona_al_instante_vigila_el_libro_y_sobrevive_al_reinicio(tmp_path):
    clock = ManualClock(T0 + 600)
    prices = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}
    mk = FakeMexc(lambda sym, m: prices[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts)
        assert "Empecé la cuenta de práctica en futuros MEXC" in alerts.texts()[0]
        await t.on_news(news("h1", HACK, clock.time()))
        pos = t.st["positions"]["BTC_USDT"]
        assert pos["side"] == "corto" and D(pos["qty"]) < 0 and "Vendí en corto BTC" in alerts.texts()[-1]
        assert state(home)["positions"]["BTC_USDT"]["side"] == "corto"           # guardado al instante
        clock.advance(2)
        await t.watch_once()
        assert t.st["positions"]                                                 # el precio no se movió: sigue
        prices["BTC_USDT"] = 98_000.0                                            # −2 %: cruzó el take-profit
        clock.advance(2)
        await t.watch_once()
        assert t.st["positions"] == {} and t.st["stats"]["ganadas"] == 1 and "cobré la ganancia" in alerts.texts()[-1]
        trades = [json.loads(x) for x in (home / "paper-vivo" / "mexc-futuros" / "operaciones" / "2026-11.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        assert [r["tipo"] for r in trades] == ["entrada", "salida"] and D(trades[-1]["pnl_neto"]) > 0

        t2 = await make(session, url, clock, home, Alerts())                    # reinicio: el estado sigue
        assert t2.st["stats"]["operaciones"] == 1 and D(t2.st["cash"]) == D(t.st["cash"])
        clock.advance(30)
        await t2.on_news(news("h2", "Kraken adds trading for Bitcoin (BTC) options", clock.time()))
        assert t2.st["positions"]["BTC_USDT"]["side"] == "largo"
        clock.advance(3601)                                                      # barrera vertical: 60 min
        await t2.watch_once()
        assert t2.st["positions"] == {} and t2.counts.get("expiracion") == 1


async def test_kill_y_noticias_viejas_no_entran(tmp_path):
    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts)
        home.mkdir(parents=True, exist_ok=True)
        (home / "KILL").write_text("")
        await t.on_news(news("k1", HACK, clock.time()))
        assert t.st["positions"] == {}
        (home / "KILL").unlink()
        await t.on_news(news("o1", HACK, clock.time(), lag=400))                # el alfa ya no vale nada
        assert t.st["positions"] == {}
    rows = [json.loads(x) for x in (home / "paper-vivo" / "mexc-futuros" / "decisiones" / "2026-11.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert rows[0]["motivos"] == ["interruptor_KILL"] and rows[1]["motivos"][0] == "alfa_obsoleta:400s"


async def test_la_ia_lee_cada_noticia_antes_de_entrar_y_puede_achicarla(tmp_path):
    """Lo que pasó en la PC el 3-nov: las reglas solas abrieron cortos con titulares mal leídos. Ahora la IA lee
    toda noticia que las reglas quieren operar; sin su lectura no se entra; su lectura solo achica el vector."""
    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    fake = FakeClaude()
    fake.reply = {**fake.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home = tmp_path / "home"
    liquid = "Liquid’s bitcoin IOUs are fully backed again after a $320 million exploit"
    async with serve(mk.app()) as url, serve(fake.app()) as aurl, aiohttp.ClientSession() as session:
        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = await make(session, url, clock, home, Alerts(), llm=llm)
            await t.on_news(news("m1", "Mercado Bitcoin pledges to cover all user losses after near $40 million "
                                       "hack", clock.time()))
            assert fake.requests == [] and t.st["positions"] == {}      # un exchange de Brasil no es BTC: ni consulta
            fake.reply = {**fake.reply, "polarity": 0.3, "relevance": 2, "importance": 3, "impact": 1}
            await t.on_news(news("l1", liquid, clock.time()))           # las reglas querían un corto: la IA lo lee
            assert len(fake.requests) == 1 and t.st["positions"] == {}
            fake.script = ["500"]
            await t.on_news(news("c0", "Bybit hacked: $90M in BTC stolen", clock.time()))   # la IA no responde:
            assert len(fake.requests) == 2 and t.st["positions"] == {}                      # no se entra
            fake.reply = {**fake.reply, "polarity": -0.9, "relevance": 10, "importance": 8, "impact": 8}
            await t.on_news(news("c1", HACK, clock.time()))             # clara y confirmada: entra con la IA
            assert len(fake.requests) == 3 and t.st["positions"]["BTC_USDT"]["side"] == "corto"
            assert state(home)["llm"]["calls"] == 2 and t.snapshot()["llm"]["calls"] == 2
            again = LlmClassifier(KEY, base_url=aurl, clock=clock)                # reinicio: el gasto del día sigue
            t2 = await make(session, url, clock, home, Alerts(), llm=again)
            assert t2.llm.budget.calls == 2 and t2.llm.budget.usd == llm.budget.usd > 0
            await again.close()
        finally:
            await llm.close()
    rows = [json.loads(x) for x in (home / "paper-vivo" / "mexc-futuros" / "decisiones" / "2026-11.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    by = {r["event_id"]: r for r in rows}
    assert by["l1"]["signal"]["rules"] == ["llm"] and by["l1"]["signal"]["relevance"] == 2
    assert "relevancia_baja:C=2" in by["l1"]["motivos"]                 # la IA achicó la relevancia: no entra
    assert by["c0"]["motivos"] == ["sin_confirmacion_ia"]
    assert by["c1"]["signal"]["version"] == "llm-claude-haiku-5-5" and by["c1"]["decision"] == "entrar"


async def test_no_paga_a_claude_si_la_respuesta_no_cambiaria_nada(tmp_path):
    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    fake = FakeClaude()
    fake.reply = {**fake.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home = tmp_path / "home"
    rumor = "Bitcoin custodian reportedly hacked, users may lose funds"
    async with serve(mk.app()) as url, serve(fake.app()) as aurl, aiohttp.ClientSession() as session:
        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = await make(session, url, clock, home, Alerts(), llm=llm)
            await t.on_news(news("vieja", rumor, clock.time(), lag=900))            # llegó tarde: no cambia nada
            (home / "KILL").write_text("")
            await t.on_news(news("kill", "Ethereum wallet reportedly hacked, funds at risk", clock.time()))
            assert fake.requests == [] and t.counts["ia_evitada"] == 2               # ni un centavo
            ev = t._event(news("x", rumor, clock.time()))
            sig = classify(rumor)
            assert t.worth_asking(ev, sig) == "kill"
            t.st["positions"]["BTC_USDT"] = {"side": "largo"}                        # con BTC abierto, sí: quizá
            assert t.worth_asking(ev, sig) == ""                                     # haya que salir a tiempo
            del t.st["positions"]["BTC_USDT"]
            (home / "KILL").unlink()
            await t.on_news(news("fresca", rumor, clock.time()))
            assert len(fake.requests) == 1                                           # fresca y operable: se paga
        finally:
            await llm.close()


async def test_resumen_diario_de_la_cuenta_de_la_pc(tmp_path):
    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts)
        await t.housekeeping()                                                       # primer día: no hay "ayer"
        assert not any("Resumen" in x for x in alerts.texts())
        ops = home / "paper-vivo" / "mexc-futuros" / "operaciones"
        ops.mkdir(parents=True, exist_ok=True)
        with (ops / "2026-11.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"tipo": "salida", "pnl_neto": "0.5", "ts": T0 + 700, "symbol": "BTC_USDT"}) + "\n")
        clock.set(T0 + 12 * 3600 + 60)                                               # 3-nov 00:01 UTC (21:01 en AR)
        await t.housekeeping()
        await t.housekeeping()                                                       # una sola vez por día
    texts = [x for x in alerts.texts() if "Resumen del 02/11" in x]
    assert len(texts) == 1
    assert "1 operación cerrada (1 ganada): +0,50 USDT." in texts[0] and "Cuenta: 120,00 USDT" in texts[0]
    assert "con 60 ya se puede medir bien si el sistema gana" in texts[0]


async def test_opera_la_accion_que_mueve_la_noticia_con_el_universo_de_mexc(tmp_path):
    """El dueño: operar "cripto, acciones o lo que sea necesario según la noticia". El universo sale del catálogo
    público de MEXC (Tesla es TESLA_USDT); las reglas reconocen "Tesla", la IA lo confirma y se opera la acción."""
    from tradingagent.research import assets

    clock = ManualClock(T0 + 600)
    prices = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0, "TESLA_USDT": 400.0}
    mk = FakeMexc(lambda sym, m: prices[sym], clock)
    fake = FakeClaude()
    fake.reply = {**fake.reply, "category": "resultados", "assets": ["TSLA"], "polarity": 0.8, "certainty": 0.9,
                  "horizon_min": 60, "relevance": 10, "importance": 7, "impact": 7}
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, serve(fake.app()) as aurl, aiohttp.ClientSession() as session:
        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = await make(session, url, clock, home, alerts, llm=llm)
            assert t.market.symbol("TSLA") == "TESLA_USDT" and assets.known("TSLA")      # del catálogo de MEXC
            assert (home / "datos" / "universo-mexc.json").exists()                     # en caché para reinicios
            await t.on_news(news("t1", "Tesla beats Q3 estimates as deliveries jump", clock.time()))
            assert len(fake.requests) == 1 and "TSLA" in fake.requests[0]["system"]      # la IA sabe qué se opera
            pos = t.st["positions"]["TESLA_USDT"]
            assert pos["side"] == "largo" and pos["category"] == "resultados"
            assert "Compré TESLA" in alerts.texts()[-1]
        finally:
            await llm.close()


async def test_el_universo_se_mira_cada_10_minutos_no_en_cada_vuelta(tmp_path):
    """3-nov en la PC: releerlo cada 30 s costaba ~17 ms del bucle por cuenta y llenaba el log."""
    from tradingagent import live

    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts)                       # al arrancar, se carga
        calls = []
        real = t.market.load_universe

        async def counting(*a, **kw):
            calls.append(clock.time())
            return await real(*a, **kw)

        t.market.load_universe = counting
        for _ in range(3):
            await t.housekeeping()                                              # vueltas de 30 s: no se relee
            clock.advance(30)
        assert calls == []
        clock.advance(live.UNIVERSE_EVERY_S)
        await t.housekeeping()
        assert len(calls) == 1


async def test_no_corre_detras_del_precio_si_la_noticia_ya_se_movio(tmp_path):
    """Si desde la publicación el precio ya recorrió el objetivo, la ganancia ya la tomaron otros; si fue en
    contra más que el stop, el mercado no le cree a la noticia. En los dos casos no se entra."""
    clock = ManualClock(T0 + 600)
    now_m = int(clock.time() * 1000) // 60_000 * 60_000
    level = {"after": 100_000.0}

    def price(sym, m):
        base = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym]
        return level["after"] if sym == "BTC_USDT" and m >= now_m else base

    mk = FakeMexc(price, clock)
    home = tmp_path / "home"
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, Alerts())
        level["after"] = 97_500.0                                     # el hackeo ya lo bajó 2,5 %: objetivo hecho
        await t.on_news(news("y1", HACK, clock.time(), lag=70))
        assert t.st["positions"] == {}
        level["after"] = 101_000.0                                    # subió 1 % pese al hackeo: el mercado no le cree
        clock.advance(2)                                              # libro nuevo (el anterior quedó en caché)
        await t.on_news(news("y2", "Bybit hacked: $90M in BTC stolen", clock.time(), lag=70))
        assert t.st["positions"] == {}
        level["after"] = 99_900.0                                     # se movió poco a favor: todavía hay recorrido
        clock.advance(2)
        await t.on_news(news("y3", "OKX hacked: $70M in BTC stolen", clock.time(), lag=70))
        assert t.st["positions"]["BTC_USDT"]["side"] == "corto"
    rows = {r["event_id"]: r for r in map(json.loads, (home / "paper-vivo" / "mexc-futuros" / "decisiones" /
                                                        "2026-11.jsonl").read_text(encoding="utf-8").splitlines())}
    assert rows["y1"]["motivos"][0].startswith("ya_recorrio_el_objetivo:+2.50")
    assert rows["y2"]["motivos"][0].startswith("el_mercado_va_en_contra:-1.00")
    assert rows["y3"]["analisis"]["movido_desde_la_noticia"] == 0.001


async def test_listado_sale_por_tiempo_con_stop_fijo_de_10(tmp_path):
    """Calibración del 4-nov (Upbit en Binance, n = 23): en los listados, stop FIJO de 10 % y salida a los 15 min. El
    sacudón después del pico (que tocaba el stop de 5 %) ya no lo saca; el riesgo sigue en 1 % (A-5)."""
    clock = ManualClock(T0 + 600)
    prices = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}
    mk = FakeMexc(lambda sym, m: prices[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts)
        await t.on_news(news("l1", "Kraken adds trading for Bitcoin (BTC) options", clock.time()))
        pos = t.st["positions"]["BTC_USDT"]
        entry = D(pos["avg_price"])
        assert "trail" not in pos and entry * D("0.899") < D(pos["stop"]) < entry * D("0.901")   # stop fijo 10 %
        assert D(pos["tp"]) > entry * 10 and pos["until"] - clock.time() == pytest.approx(900, abs=1)
        assert "salgo cuando se cumple el tiempo" in alerts.texts()[-1]
        for px in (112_000.0, 94_000.0, 103_000.0):          # pico, sacudón de −6 % y rebote: no sale
            prices["BTC_USDT"] = px
            clock.advance(60)
            await t.watch_once()
        assert "BTC_USDT" in t.st["positions"]
        clock.advance(900)                                    # a los 15 min sale con lo que haya
        await t.watch_once()
        assert t.st["positions"] == {} and "Gané" in alerts.texts()[-1]
        await t.on_news(news("l2", "Coinbase adds trading for Bitcoin (BTC) perpetuals", clock.time()))
        entry2 = D(t.st["positions"]["BTC_USDT"]["avg_price"])
        prices["BTC_USDT"] = float(entry2) * 0.893              # −10,7 %: toca el stop fijo
        clock.advance(5)
        await t.watch_once()
        assert t.st["positions"] == {} and any("Perdí" in x for x in alerts.texts()[-2:])
        # con 3 % de riesgo, un stop completo pasa el freno del día (2 %): no abre más hasta la noche
        assert "Freno del día" in alerts.texts()[-1]


async def test_solo_baja_datos_de_mercado_para_lo_que_puede_entrar(tmp_path):
    """Diario de la PC (3 y 4-nov): bajar contrato y 130 min de velas para cada noticia, con el candado tomado,
    demoraba hasta 14 s a la siguiente. Ahora una noticia descartada por relevancia o alfa no pide nada a MEXC."""
    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts)
        calls = []
        real = t.market.instruments

        async def counting(*a, **kw):
            calls.append(a[1])
            return await real(*a, **kw)

        t.market.instruments = counting
        await t.on_news(news("w1", "SEC sues crypto exchange over unregistered securities", clock.time()))
        assert calls == [] and t.st["positions"] == {}                   # se descartó sin pedir datos
        await t.on_news(news("h1", HACK, clock.time()))                     # la que sí entra pide sus datos
        assert calls and "BTC_USDT" in calls[-1] and "BTC_USDT" in t.st["positions"]
