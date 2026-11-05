"""Deslistados de Binance (decision/delistings.py) contra un Binance falso: apenas llega el aviso oficial "Binance Will
Delist ...", corto en el perpetuo si el precio todavía no cayó más de 4 %, con stop fijo de 20 % y salida a las 4 h,
arriesgando 2 % del saldo. Sin IA: el título es inequívoco y el precio cae a los 2-5 s (estudio del 4-nov)."""
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
from tradingagent.decision import delistings
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.live import LiveTrader
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import minute

T0 = calendar.timegm((2026, 11, 5, 9, 0, 58))          # el aviso, a los 58 s de un minuto
TITLE = "Binance Will Delist SOL, FOO and BAR on 2026-11-20"


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)


def notice(nid, title=TITLE, source="binance.ws", published=T0):
    it = NewsItem(id=nid, source=source, tier=1, kind="websocket", title=title, body="", published_at=published,
                  received_at=published + 1)
    return IngestedNews(item=it, age_s=1.0, freshness="fresh", dedup=DedupResult("new", nid),
                        noise=NoiseResult(0, False), accepted=True)


def market(after: float):
    """SOL a 150 hasta el minuto del aviso y a `after` desde el siguiente."""
    return lambda sym, m: (after if m > minute(T0) else 150.0) if sym == "SOLUSDT" else PRICES.get(sym)


async def trader(session, url, clock, home):
    t = LiveTrader(BinanceFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=Alerts(),
                   home=home, capital=120.0, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)))
    await t.start()
    return t


def decisions(home):
    f = home / "paper-vivo" / "binance-futuros" / "decisiones" / "2026-11.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]


def test_solo_los_avisos_de_deslistado_de_binance():
    assert delistings.coins("binance.ws", TITLE) == ("SOL", "FOO", "BAR")
    assert delistings.coins("binance.ws", "Binance Will Delist DAR, KDA, LOOM on 2025-06-16") == ("DAR", "KDA", "LOOM")
    assert delistings.coins("binance.ws", "Binance Margin Will Delist SOL/BTC Margin Pairs") == ()
    assert delistings.coins("binance.ws", "Binance Futures Will Delist USDⓈ-M SOLUSDT Perpetual Contract") == ()
    assert delistings.coins("coindesk", TITLE) == ()                    # solo el aviso oficial


async def test_si_todavia_no_cayo_va_corto_con_stop_de_20_y_salida_a_las_4_horas(tmp_path):
    clock = ManualClock(T0 + 3)                                         # 3 s después, ya en el minuto siguiente
    fake = FakeBinance(price=market(149.0), clock=clock)                # −0,7 %: todavía no cayó
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        before = len(fake.calls)
        await t.on_news(notice("d1"))
        await asyncio.gather(*t.pending)
        books = [c for c in fake.calls[before:] if c[1] in ("/fapi/v1/depth", "/fapi/v1/ticker/bookTicker")]
        assert len(books) == 1                                              # un solo viaje por el libro: ~0,3 s menos
        pos = t.st["positions"]["SOLUSDT"]
        assert pos["side"] == "corto" and pos["category"] == "deslistado_corto"
        entry = float(pos["avg_price"])
        assert 1.19 < float(pos["stop"]) / entry < 1.21                     # stop fijo de 20 %
        assert abs(pos["until"] - clock.time() - 4 * 3600) < 5               # sale a las 4 h
        assert entry * float(pos["qty"]) <= 0.10 * 120 + 0.5                # 2 % del saldo al stop
        assert len(t.st["positions"]) == 1                                   # FOO y BAR no tienen perpetuo
    rows = decisions(home)
    assert next(r for r in rows if r["decision"] == "entrar")["signal"]["version"] == "deslistado-1"
    assert any("lo_opera_la_regla_de_deslistados" in r["motivos"] for r in rows if r["decision"] == "no_entrar")


async def test_si_ya_cayo_llegamos_tarde_y_no_entra(tmp_path):
    clock = ManualClock(T0 + 8)
    fake = FakeBinance(price=market(140.0), clock=clock)                # −6,7 %: ya pasó
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("d2"))
        await asyncio.gather(*t.pending)
        assert t.st["positions"] == {}
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "deslistado_corto"]
    assert why and why[0][0].startswith("ya_cayo:-6.67")
