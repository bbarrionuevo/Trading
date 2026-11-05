"""Resultados de empresas con impulso (decision/earnings.py) contra un Binance falso con perpetuos TradFi: el 8-K de
resultados de la SEC (ítem 2.02) se mide a los 2 minutos y, si el perpetuo ya se movió ≥ 0,5 %, se entra en esa
dirección con stop fijo de 5 % y salida a los 30 min. Sin IA: la dirección la da el precio (estudio del 4-nov)."""
import asyncio
import calendar
import copy
import json

import aiohttp
from aiohttp import web

from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.decision import earnings
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.live import LiveTrader
from tradingagent.marketdata import sec_tickers
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import MIN, minute

T0 = calendar.timegm((2026, 11, 4, 21, 0, 0))
TESLA = "8-K - Tesla, Inc. (0001318605) (Filer)"
ITEMS = ("Filed: 2026-11-04 AccNo: 0001318605-26-000099 Size: 2 MB Item 2.02: Results of Operations and Financial "
         "Condition Item 9.01: Financial Statements and Exhibits")


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)


def filing(nid, published, received, title=TESLA, body=ITEMS):
    it = NewsItem(id=nid, source="sec.edgar.8k", tier=1, kind="rss", title=title, body=body, published_at=published,
                  received_at=received)
    return IngestedNews(item=it, age_s=received - published, freshness="fresh", dedup=DedupResult("new", nid),
                        noise=NoiseResult(0, False), accepted=True)


def market(jump_at: int, after: float):
    """TSLA a 400 hasta el minuto del 8-K y a `after` desde el siguiente (el cierre de antes queda en 400)."""
    return lambda sym, m: (after if m > jump_at else 400.0) if sym == "TSLAUSDT" else PRICES.get(sym)


async def trader(session, url, clock, home):
    t = LiveTrader(BinanceFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=Alerts(),
                   home=home, capital=120.0, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)))
    await t.start()
    t._ciks, t._ciks_at = {1318605: ("TSLA",), 1045810: ("NVDA",)}, clock.time()     # sin pedir la lista a la SEC
    return t


def decisions(home):
    f = home / "paper-vivo" / "binance-futuros" / "decisiones" / "2026-11.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]


def test_reconoce_el_8k_de_resultados_y_la_empresa():
    assert earnings.filing_cik("sec.edgar.8k", TESLA, ITEMS) == 1318605
    assert earnings.filing_cik("sec.edgar.8k", TESLA, "Item 7.01: Regulation FD Disclosure") is None
    assert earnings.filing_cik("coindesk", TESLA, ITEMS) is None
    sig, a = earnings.signal("TSLA", -1), earnings.assessment("TSLA", -1, 125.0)
    assert sig.category == "resultados_impulso" and sig.polarity < 0 and a.side == -1 and a.p == 0.65 and not a.reasons


async def test_a_los_2_minutos_sigue_el_impulso_con_stop_de_5_y_salida_a_los_30(tmp_path):
    clock = ManualClock(T0 + 125)
    t0 = T0                                                         # el 8-K se aceptó hace 125 s
    fake = FakeBinance(price=market(minute(t0), 404.0), clock=clock)  # +1 % desde el comunicado
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(filing("k1", t0, t0 + 60))
        await asyncio.gather(*t.pending)
        pos = t.st["positions"]["TSLAUSDT"]
        assert pos["side"] == "largo" and pos["category"] == "resultados_impulso"
        entry = float(pos["avg_price"])
        assert 0.949 < float(pos["stop"]) / entry < 0.951                 # stop fijo de 5 %
        assert abs(pos["until"] - clock.time() - 1800) < 2                # sale a los 30 min
    row = next(r for r in decisions(home) if r["decision"] == "entrar")
    assert row["symbol"] == "TSLAUSDT" and row["signal"]["version"] == "impulso-resultados-1"


async def test_sin_impulso_tarde_o_sin_perpetuo_no_entra(tmp_path):
    clock = ManualClock(T0 + 125)
    fake = FakeBinance(price=market(minute(T0), 400.8), clock=clock)  # +0,2 %: no alcanza
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(filing("k1", T0, T0 + 60))
        await t.on_news(filing("k2", T0 - 300, T0 - 240))                # llegó tarde: pasaron 7 min
        await t.on_news(filing("k3", T0, T0 + 60, title="8-K - NVIDIA CORP (0001045810) (Filer)"))  # sin perpetuo
        await t.on_news(filing("k4", T0, T0 + 60, title="8-K - Acme Corp (0000000001) (Filer)"))    # sin ticker
        await asyncio.gather(*t.pending)
        assert t.st["positions"] == {}
    why = {r["event_id"]: r["motivos"] for r in decisions(home) if r["signal"].get("category") == "resultados_impulso"}
    assert why["k1"][0].startswith("impulso_chico:+0.20") and why["k2"][0].startswith("resultados_tarde:")
    assert "k3" not in why and "k4" not in why


async def test_la_lista_de_tickers_de_la_sec_se_guarda_y_pide_el_user_agent(tmp_path):
    seen = []

    async def handler(request):
        seen.append(request.headers.get("User-Agent"))
        return web.json_response({"0": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
                                  "1": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
                                  "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."}})

    app = web.Application()
    app.router.add_get("/files/company_tickers.json", handler)
    cache = tmp_path / "sec-tickers.json"
    async with serve(app) as url, aiohttp.ClientSession() as s:
        assert await sec_tickers.load(s, cache, None, url=f"{url}/files/company_tickers.json") == {}   # sin UA
        m = await sec_tickers.load(s, cache, "TRADING contacto@example.com", url=f"{url}/files/company_tickers.json",
                                   now=T0)
        again = await sec_tickers.load(s, cache, "TRADING contacto@example.com", url=f"{url}/files/company_tickers.json",
                                       now=T0 + 3600)                                              # de la caché
    assert m == again == {1652044: ("GOOGL", "GOOG"), 1318605: ("TSLA",)}
    assert seen == ["TRADING contacto@example.com"]
    assert earnings.MAX_LATE_S < 300 and 1 * MIN == 60_000


def test_la_sec_lenta_se_reintenta_rapido():
    """La SEC a veces tarda más de 10 s sin estar caída: se espera hasta 25 s y nunca se deja de leer más de 1 min
    (con la espera que se duplicaba, 4 fallas seguidas dejaban ~10 min sin la SEC)."""
    from tradingagent.ingestion import catalog

    spec = next(s for s in catalog.DEFAULT_SOURCES if s.id == "sec.edgar.8k")
    assert spec.timeout_s >= 25 and spec.max_backoff_s <= 60 and spec.open_after >= 20 and spec.cooldown_s <= 120
