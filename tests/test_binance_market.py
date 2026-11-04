"""Práctica sobre Binance Futures (datos públicos, sin claves) contra un Binance falso: universo de cripto y TradFi,
contratos, libro, velas y funding, y el operador en vivo operando lo que mueve la noticia (el dueño, 4-nov: "invertir
en todo: acciones, criptos, monedas")."""
import calendar
import copy
import json
from decimal import Decimal as D

import aiohttp

from conftest import serve
from fake_anthropic import KEY, FakeClaude
from fake_binance import PRICES, FakeBinance
from tradingagent import config
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.intelligence.llm import LlmClassifier
from tradingagent.live import LiveTrader
from tradingagent.marketdata import universe
from tradingagent.research import assets
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import MIN, minute

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


async def make(session, url, clock, home, alerts, llm=None, capital=120.0):
    t = LiveTrader(BinanceFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=alerts, home=home,
                   capital=capital, llm=llm, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)))
    await t.start()
    return t


async def test_universo_de_binance_cripto_y_tradfi(tmp_path):
    fake = FakeBinance(clock=ManualClock(T0))
    cache = tmp_path / "universo-binance.json"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        m = BinanceFutures(base=url)
        assert await m.load_universe(s, cache, now=T0) == len(m.universe) > 0
    u = m.universe
    assert m.symbol("PEPE") == "1000PEPEUSDT" and u.kind("PEPE") == "cripto"            # sin el multiplicador
    assert m.symbol("TSLA") == "TSLAUSDT" and u.kind("TSLA") == "accion"
    assert m.symbol("SILVER") == m.symbol("XAG") == "XAGUSDT" and u.kind("XAG") == "materia_prima"
    assert m.symbol("SP500") == m.symbol("SPY") == "SPYUSDT" and u.kind("SPY") == "indice"
    assert not {"OPENAI", "BTCDOM", "TINY"} & u.keys()      # sin cotizar todavía, índice de cripto y sin volumen
    assert "SMALL" in u.keys() and "SMALL" not in u.liquid_keys()
    assert assets.detect("Tesla jumps as gold prices slide") == ["TSLA", "XAU"]
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["alias"]["SPY"] == "SP500" and universe.Universe.from_dict(cached).symbol("XAG") == "XAGUSDT"


async def test_contratos_libro_velas_y_funding_de_binance():
    clock = ManualClock(T0 + 600)
    fake = FakeBinance(clock=clock)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        m = BinanceFutures(base=url)
        reg = await m.instruments(s, ["BTCUSDT", "XAUUSDT", "OPENAIUSDT"], {}, clock.time())
        btc, gold = reg.get("BTCUSDT"), reg.get("XAUUSDT")
        assert (btc.tick_size, btc.step_size, btc.min_notional) == (D("0.1"), D("0.001"), D("50"))
        assert gold.max_leverage == 1 and gold.taker_fee_bps == 5 and reg.get("OPENAIUSDT") is None
        book = await m.books(s, clock).book("XAUUSDT")
        assert book.best_bid < book.best_ask and float(book.spread_bps) < 5
        tick = await m.ticker(s, "XAUUSDT")
        assert tick["last"] == tick["fair"] == 4000.0 and tick["funding_rate"] == 0.0001 and tick["adv"] == 2e9
        assert (await m._fetch_funding(s, "XAUUSDT"))["cycle_h"] == 4                      # el oro cobra cada 4 h
        assert (await m._fetch_funding(s, "BTCUSDT"))["cycle_h"] == 8
        store = m.candle_store(s)
        now_m = minute(clock.time())
        await store.load({"TSLAUSDT": [(now_m - 130 * MIN, now_m)]})
        assert len(store.candles["TSLAUSDT"]) == 131 and store.source["TSLAUSDT"] == "binance-futuros"


async def test_un_contrato_nuevo_se_busca_en_el_catalogo_como_mucho_una_vez_por_minuto():
    """Un listado nuevo: el contrato puede no estar en el catálogo de memoria. Se vuelve a pedir, sin martillar."""
    clock = ManualClock(T0)
    fake = FakeBinance(clock=clock)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        m = BinanceFutures(base=url)
        await m.instruments(s, ["BTCUSDT"], {}, clock.time())
        asked = lambda: sum(1 for _, p in fake.calls if p == "/fapi/v1/exchangeInfo")     # noqa: E731
        assert asked() == 1
        cache = {}
        await m.instruments(s, ["NUEVOUSDT"], cache, clock.time() + 10)                   # recién: no repite
        assert asked() == 1
        await m.instruments(s, ["NUEVOUSDT"], cache, clock.time() + 61)                   # pasó un minuto
        assert asked() == 2 and "NUEVOUSDT" not in cache


async def test_btc_en_binance_pide_al_menos_50_usdt_por_orden(tmp_path):
    """En Binance, BTC tiene un nominal mínimo de 50 USDT (el resto, 5 a 20): con 120 USDT y 3 % de riesgo, el corto
    del hackeo sería de 36 USDT y no entra."""
    clock = ManualClock(T0 + 600)
    async with serve(FakeBinance(clock=clock).app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, tmp_path / "home", Alerts())
        await t.on_news(news("h1", HACK, clock.time()))
        assert t.st["positions"] == {}
    rows = [json.loads(x) for x in (tmp_path / "home" / "paper-vivo" / "binance-futuros" / "decisiones" / "2026-11.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert rows[0]["motivos"][0].startswith("nominal_menor_al_minimo")


async def test_la_practica_en_binance_opera_cripto_y_vigila_el_libro(tmp_path):
    clock = ManualClock(T0 + 600)
    prices = dict(PRICES)
    fake = FakeBinance(price=lambda sym, m: prices.get(sym), clock=clock)
    home, alerts = tmp_path / "home", Alerts()
    async with serve(fake.app()) as url, aiohttp.ClientSession() as session:
        t = await make(session, url, clock, home, alerts, capital=1000.0)
        assert "Empecé la cuenta de práctica en futuros Binance" in alerts.texts()[0]
        await t.on_news(news("h1", HACK, clock.time()))
        pos = t.st["positions"]["BTCUSDT"]
        assert pos["side"] == "corto" and D(pos["qty"]) < 0 and "Vendí en corto BTC" in alerts.texts()[-1]
        assert D(pos["qty"]) * D(pos["avg_price"]) <= D(-50)                           # el mínimo de BTC: 50 USDT
        prices["BTCUSDT"] = 98_000.0                                                    # −2 %: cruzó el objetivo
        clock.advance(2)
        await t.watch_once()
        assert t.st["positions"] == {} and t.st["stats"]["ganadas"] == 1
    saved = json.loads((home / "paper-vivo" / "binance-futuros" / "estado.json").read_text(encoding="utf-8"))
    assert saved["mercado"] == "binance-futuros" and saved["stats"]["operaciones"] == 1
    assert (home / "datos" / "universo-binance.json").exists()


async def test_la_practica_en_binance_opera_la_accion_que_mueve_la_noticia(tmp_path):
    """Tesla en Binance es un perpetuo TradFi (TSLAUSDT) que opera las 24 h: las reglas reconocen "Tesla", la IA lo
    confirma y se opera la acción."""
    clock = ManualClock(T0 + 600)
    fake = FakeBinance(clock=clock)
    claude = FakeClaude()
    claude.reply = {**claude.reply, "category": "resultados", "assets": ["TSLA"], "polarity": 0.8, "certainty": 0.9,
                    "horizon_min": 60, "relevance": 10, "importance": 7, "impact": 7}
    home, alerts = tmp_path / "home", Alerts()
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as session:
        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = await make(session, url, clock, home, alerts, llm=llm)
            assert t.market.symbol("TSLA") == "TSLAUSDT" and assets.known("TSLA")
            await t.on_news(news("t1", "Tesla beats Q3 estimates as deliveries jump", clock.time()))
            assert len(claude.requests) == 1 and "TSLA" in claude.requests[0]["system"]
            pos = t.st["positions"]["TSLAUSDT"]
            assert pos["side"] == "largo" and pos["category"] == "resultados"
            assert "Compré TSLA" in alerts.texts()[-1]
            assert any("largo TSLA desde" in x for x in t.status_lines("Práctica"))
        finally:
            await llm.close()


def test_la_practica_va_en_binance_por_defecto():
    cfg = config.load(None)
    assert cfg["trader"]["mercado"] == "binance-futuros" and config.validate(cfg) == []
    bad = copy.deepcopy(cfg)
    bad["trader"]["mercado"] = "kraken"
    assert any("trader.mercado" in e for e in config.validate(bad))
