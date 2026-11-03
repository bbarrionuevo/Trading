"""Operador paper EN VIVO (live.py) contra un MEXC falso: reacciona a cada noticia al instante, vigila las
barreras con el libro, sobrevive a un reinicio, respeta KILL y consulta a Claude solo lo dudoso."""
import calendar
import copy
import json
from decimal import Decimal as D

import aiohttp

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


async def test_consulta_a_claude_solo_lo_dudoso_y_usa_su_opinion(tmp_path):
    clock = ManualClock(T0 + 600)
    mk = FakeMexc(lambda sym, m: {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}[sym], clock)
    fake = FakeClaude()
    fake.reply = {**fake.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home = tmp_path / "home"
    async with serve(mk.app()) as url, serve(fake.app()) as aurl, aiohttp.ClientSession() as session:
        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = await make(session, url, clock, home, Alerts(), llm=llm)
            await t.on_news(news("c1", HACK, clock.time()))                     # clara: no se consulta
            assert fake.requests == []
            await t.on_news(news("r1", "Bitcoin custodian reportedly hacked, users may lose funds", clock.time()))
            assert len(fake.requests) == 1
            assert state(home)["llm"]["calls"] == 1 and t.snapshot()["llm"]["calls"] == 1
            again = LlmClassifier(KEY, base_url=aurl, clock=clock)                # reinicio: el gasto del día sigue
            t2 = await make(session, url, clock, home, Alerts(), llm=again)
            assert t2.llm.budget.calls == 1 and t2.llm.budget.usd == llm.budget.usd > 0
            await again.close()
        finally:
            await llm.close()
    rows = [json.loads(x) for x in (home / "paper-vivo" / "mexc-futuros" / "decisiones" / "2026-11.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["signal"]["version"] == "llm-claude-haiku-5-5" and rows[-1]["signal"]["rules"] == ["llm"]


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
    assert "Para pasar a dinero real hacen falta 60 operaciones" in texts[0]
