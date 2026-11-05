"""Listados de Upbit al instante (decision/upbit_listings.py, estudio del 5-nov: estudios/upbit_segundos.py): largo en
el perpetuo apenas llega el aviso por Telegram (≤ 3 s), si el precio no subió más que el tope de Binance (+16 %), sin
esperar a la IA ni las alarmas del libro (B-4); la IA lee después y, si no confirma, se cierra."""
import asyncio
import calendar
import copy
import json

import aiohttp

from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.decision import upbit_listings
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.intelligence.classifier import Signal
from tradingagent.live import LiveTrader
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import minute

T0 = calendar.timegm((2026, 11, 5, 9, 0, 58))          # el mensaje de Telegram, a los 58 s de un minuto
TITLE = "솔라나(SOL), 푸코인(FOO) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)


class FakeLlm:
    enabled = True

    def __init__(self, signal):
        self.signal, self.budget, self.calls = signal, None, 0

    async def classify(self, title, body, fallback):
        from tradingagent.intelligence.llm import LlmResult
        self.calls += 1
        return LlmResult(self.signal)


def notice(nid, title=TITLE, published=T0):
    it = NewsItem(id=nid, source="upbit.avisos", tier=1, kind="relay", title=title, body="", published_at=published,
                  received_at=published + 0.2)
    return IngestedNews(item=it, age_s=0.2, freshness="fresh", dedup=DedupResult("new", nid),
                        noise=NoiseResult(0, False), accepted=True)


def market(after: float):
    """SOL a 150 hasta el minuto del aviso y a `after` desde el siguiente."""
    return lambda sym, m: (after if m > minute(T0) else 150.0) if sym == "SOLUSDT" else PRICES.get(sym)


async def trader(session, url, clock, home, llm=None):
    t = LiveTrader(BinanceFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=Alerts(),
                   home=home, capital=120.0, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)), llm=llm)
    await t.start()
    return t


async def settle(t):
    while t.pending:
        await asyncio.gather(*list(t.pending))


def rows(home, kind="decisiones"):
    f = home / "paper-vivo" / "binance-futuros" / kind / "2026-11.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_solo_los_listados_de_upbit_con_las_reglas_del_estudio():
    assert upbit_listings.notice("upbit.avisos", TITLE) == ("listado_spot", ("SOL", "FOO"))
    assert upbit_listings.notice("upbit.avisos", "엘리시아(ELA) KRW 마켓 디지털 자산 추가") == ("listado_krw", ("ELA",))
    assert upbit_listings.notice("upbit.avisos", "웨이브(WAVES) 거래지원 종료 안내")[0] is None       # deslistado
    assert upbit_listings.notice("upbit.avisos", "유의 종목 지정 안내 (FOO)")[0] is None             # advertencia
    assert upbit_listings.notice("upbit.avisos", "유의 종목 지정 해제 안내 (FOO)")[0] is None         # levantada
    assert upbit_listings.notice("coindesk", TITLE) == (None, ())                                  # solo Upbit


async def test_llega_a_tiempo_y_entra_largo_aunque_el_libro_dispare_las_alarmas(tmp_path):
    clock = ManualClock(T0 + 1.5)                                      # 1,5 s después del mensaje
    fake = FakeBinance(price=market(160.0), clock=clock, spread_bps=80)  # spread de 80 bps: B-4 frenaría
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("u1"))
        await settle(t)
        pos = t.st["positions"]["SOLUSDT"]
        assert pos["side"] == "largo" and pos["category"] == "listado_upbit"
        entry = float(pos["avg_price"])
        assert 0.89 < float(pos["stop"]) / entry < 0.91                     # stop fijo de 10 %
        assert abs(pos["until"] - clock.time() - 15 * 60) < 5                # sale a los 15 min
        assert entry * float(pos["qty"]) <= 0.10 * 120 + 0.5                # 1 % del saldo al stop
        assert len(t.st["positions"]) == 1                                   # FOO no tiene perpetuo
    got = rows(home)
    assert next(r for r in got if r["decision"] == "entrar")["signal"]["version"] == "listado-upbit-1"
    assert any("lo_opera_la_regla_de_listados_de_upbit" in r["motivos"] for r in got if r["decision"] == "no_entrar")


async def test_si_ya_subio_mas_que_el_tope_de_binance_no_entra(tmp_path):
    clock = ManualClock(T0 + 2.5)                                      # ya en el minuto siguiente
    fake = FakeBinance(price=market(180.0), clock=clock)                # +20 %: se escapó
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("u2"))
        await settle(t)
        assert not any(p["category"] == "listado_upbit" for p in t.st["positions"].values())
    why = [r["motivos"] for r in rows(home) if r["signal"].get("category") == "listado_upbit"]
    assert why and why[0][0].startswith("ya_subio:+20.00")


async def test_el_sondeo_del_sitio_llega_tarde_y_no_usa_la_regla_al_instante(tmp_path):
    clock = ManualClock(T0 + 8)                                        # ~8 s: el lector del sitio de Upbit
    fake = FakeBinance(price=market(151.0), clock=clock)
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("u3"))
        await settle(t)
        assert not any(p["category"] == "listado_upbit" for p in t.st["positions"].values())
    assert not any(r["signal"].get("category") == "listado_upbit" for r in rows(home))
    assert not any("lo_opera_la_regla_de_listados_de_upbit" in r["motivos"] for r in rows(home))


async def test_si_la_ia_no_confirma_el_listado_se_cierra(tmp_path):
    clock = ManualClock(T0 + 1.5)
    llm = FakeLlm(Signal("advertencia", -0.6, 0.6, ("SOL",), 60))      # la IA lee otra cosa
    async with serve(FakeBinance(price=market(160.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, tmp_path / "home", llm)
        await t.on_news(notice("u4"))
        await settle(t)
        assert not any(p["category"] == "listado_upbit" for p in t.st["positions"].values()) and llm.calls >= 1
    exits = [r for r in rows(tmp_path / "home", "operaciones") if r.get("tipo") == "salida"]
    assert exits and exits[0]["motivo"] == "la_ia_no_confirma"


async def test_si_la_ia_confirma_el_listado_queda_abierto(tmp_path):
    clock = ManualClock(T0 + 1.5)
    llm = FakeLlm(Signal("listado", 0.8, 0.7, ("SOL",), 15))
    async with serve(FakeBinance(price=market(160.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, tmp_path / "home", llm)
        await t.on_news(notice("u5"))
        await settle(t)
        assert t.st["positions"]["SOLUSDT"]["category"] == "listado_upbit"
    ab = [json.loads(x) for x in (tmp_path / "home" / "paper-vivo" / "binance-futuros" / "variante-ia.jsonl")
          .read_text(encoding="utf-8").splitlines()]                     # la prueba A/B: ¿y si se esperaba a la IA?
    assert ab[0]["confirma"] is True and ab[0]["contesto"] is True and ab[0]["symbol"] == "SOLUSDT"
    assert float(ab[0]["precio_con_la_ia"]) > 0 and ab[0]["entrada_al_instante"] and ab[0]["ia_s"] >= 0
