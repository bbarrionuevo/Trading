"""Listados de Binance al instante (decision/binance_listings.py, estudio del 6-nov: estudios/binance_listados_segundos.py):
largo en el perpetuo apenas llega el aviso oficial, si llegó a ≤ 2,2 s de publicado y el precio no subió más de 8 %, sin
esperar a la IA ni las alarmas del libro (B-4); la IA lee después y, si no confirma, se cierra."""
import asyncio
import calendar
import copy
import json
import time

import aiohttp

from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.decision import binance_listings
from tradingagent.execution.live import arming
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.intelligence.classifier import Signal
from tradingagent.live import LiveTrader
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import minute

T0 = calendar.timegm((2026, 11, 6, 10, 0, 59)) + 0.416      # publicado (con milisegundos); +1,2 s ya es otro minuto
TITLE = "Binance Will List Solana (SOL) and Foo Coin (FOO) with Seed Tag Applied"
KEY = "clave-de-prueba"


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


def notice(nid, title=TITLE, published=T0, received=None, source="binance.ws"):
    it = NewsItem(id=nid, source=source, tier=1, kind="websocket", title=title, body="", published_at=published,
                  received_at=received if received is not None else (published or T0) + 1.17)
    return IngestedNews(item=it, age_s=1.17, freshness="fresh", dedup=DedupResult("new", nid),
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


def test_solo_los_listados_de_binance_con_las_reglas_del_estudio():
    n = binance_listings.notice
    assert n("binance.ws", TITLE) == ("listado_spot", ("SOL", "FOO"))
    assert n("binance.listados", "Introducing Foo (FOO) on Binance HODLer Airdrops! Earn FOO With Retroactive BNB "
             "Simple Earn Subscriptions") == ("hodler_airdrop", ("FOO",))
    assert n("binance.ws", "Binance Will List Cheems (1000CHEEMS) and Test (TST) with Seed Tag Applied") == (
        "listado_spot", ("CHEEMS", "TST"))                                    # el universo no lleva el multiplicador
    assert n("binance.ws", "Binance Announced the First Batch of Vote to List Results and Will List Mubarak "
             "(MUBARAK), Tutorial (TUT)") == ("listado_spot", ("MUBARAK", "TUT"))
    assert n("binance.ws", "Binance Futures Will Launch USDⓈ-Margined FOOUSDT Perpetual Contract")[0] is None
    assert n("binance.ws", "Binance Futures Will List USDⓈ-M & COIN-M Quarterly 0926 Delivery Contracts")[0] is None
    assert n("binance.ws", "Binance Will List Ripple USD (RLUSD) and Introduce RLUSD Zero Trading Fee Promotion")[0] is None
    assert n("binance.ws", "Binance Will Delist FOO, BAR on 2026-11-20")[0] is None
    assert n("binance.ws", "Binance Will List Foo (FOO) on Binance Alpha")[0] is None          # no estuvo en el estudio
    assert n("coindesk", TITLE) == (None, ())                                                  # solo Binance


async def test_llega_a_tiempo_y_entra_largo_aunque_el_libro_dispare_las_alarmas(tmp_path):
    clock = ManualClock(T0 + 1.2)                                      # el canal llegó a 1,17 s de publicado
    fake = FakeBinance(price=market(155.0), clock=clock, spread_bps=80)  # spread de 80 bps: B-4 frenaría
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("b1"))
        await settle(t)
        pos = t.st["positions"]["SOLUSDT"]
        assert pos["side"] == "largo" and pos["category"] == "listado_binance"
        entry = float(pos["avg_price"])
        assert 0.89 < float(pos["stop"]) / entry < 0.91                     # stop fijo de 10 %
        assert abs(pos["until"] - clock.time() - 15 * 60) < 5                # sale a los 15 min
        assert 0.20 * 120 < entry * float(pos["qty"]) <= 0.30 * 120 + 0.5   # 3 % del saldo al stop, como Upbit
        assert len(t.st["positions"]) == 1                                   # FOO no tiene perpetuo
    got = rows(home)
    assert next(r for r in got if r["decision"] == "entrar")["signal"]["version"] == "listado-binance-1"
    assert any("lo_opera_la_regla_de_listados_de_binance" in r["motivos"] for r in got if r["decision"] == "no_entrar")


async def test_si_llega_tarde_no_usa_la_regla_al_instante(tmp_path):
    clock = ManualClock(T0 + 2.6)                                      # como el aviso a hora redonda del 6-nov
    async with serve(FakeBinance(price=market(151.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, tmp_path / "home")
        await t.on_news(notice("b2", received=T0 + 2.57))
        await settle(t)
        assert not any(p["category"] == "listado_binance" for p in t.st["positions"].values())
    assert not any(r["signal"].get("category") == "listado_binance" for r in rows(tmp_path / "home"))
    assert not any("lo_opera_la_regla_de_listados_de_binance" in r["motivos"] for r in rows(tmp_path / "home"))


async def test_sin_hora_de_publicacion_no_hay_regla_al_instante(tmp_path):
    clock = ManualClock(T0 + 1.2)
    async with serve(FakeBinance(price=market(151.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, tmp_path / "home")
        await t.on_news(notice("b3", published=None, received=T0 + 1.17))
        await settle(t)
        assert not any(p["category"] == "listado_binance" for p in t.st["positions"].values())


async def test_si_ya_subio_mas_de_8_por_ciento_no_entra(tmp_path):
    clock = ManualClock(T0 + 1.2)
    fake = FakeBinance(price=market(170.0), clock=clock)                # +13 %: el salto ya pasó
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("b4"))
        await settle(t)
        assert not any(p["category"] == "listado_binance" for p in t.st["positions"].values())
    why = [r["motivos"] for r in rows(home) if r["signal"].get("category") == "listado_binance"]
    assert why and why[0][0].startswith("ya_subio:+13.33")


async def test_si_la_ia_no_confirma_el_listado_se_cierra(tmp_path):
    clock = ManualClock(T0 + 1.2)
    llm = FakeLlm(Signal("noticias", 0.1, 0.5, ("BNB",), 60))         # la IA lee otra cosa
    async with serve(FakeBinance(price=market(155.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, tmp_path / "home", llm)
        await t.on_news(notice("b5"))
        await settle(t)
        assert not any(p["category"] == "listado_binance" for p in t.st["positions"].values()) and llm.calls >= 1
    exits = [r for r in rows(tmp_path / "home", "operaciones") if r.get("tipo") == "salida"]
    assert exits and exits[0]["motivo"] == "la_ia_no_confirma"


async def test_la_ia_confirma_aunque_nombre_la_moneda_con_el_multiplicador(tmp_path):
    clock = ManualClock(T0 + 1.2)
    llm = FakeLlm(Signal("listado", 0.8, 0.7, ("1000SOL",), 60))      # "1000SOL": la misma moneda
    async with serve(FakeBinance(price=market(155.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, tmp_path / "home", llm)
        await t.on_news(notice("b6"))
        await settle(t)
        assert t.st["positions"]["SOLUSDT"]["category"] == "listado_binance" and llm.calls >= 1


def test_con_plata_solo_si_el_dueno_la_arma_y_el_simulacro_la_suelta(tmp_path):
    def arm(strategies):
        time.sleep(0.02)
        arming.arm(tmp_path, 3, KEY, arming.phrase(3), venue="binance-futuros", strategies=strategies)

    real = LiveTrader(BinanceFutures(), None, ManualClock(time.time()), limits_mod.load(), home=tmp_path, real=True)
    side = LiveTrader(BinanceFutures(), None, ManualClock(time.time()), limits_mod.load(), home=tmp_path, real=True,
                      simulated=True, arm_file=arming.SIM_FILE, exclude_real=True)
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100.0, venue="binance-futuros", mode="local",
               file=arming.SIM_FILE)
    arm(("listado_upbit",))                                             # el armado de hoy: solo Upbit con plata
    assert not real.allows("listado_binance") and side.allows("listado_binance")
    arm(("listado_upbit", "listado_binance"))                           # el dueño la suma con su frase
    assert real.allows("listado_binance") and not side.allows("listado_binance")


async def test_guarda_lo_que_sirve_para_mejorar(tmp_path):
    """Mejora continua (el dueño, 6-nov): la decisión guarda el aviso (demora del canal), el camino hasta la orden, el
    precio de referencia y los frenos; la salida, el camino del precio (pico, piso y retorno a los 30 s...)."""
    now = {"px": 155.0}
    clock = ManualClock(T0 + 1.2)

    def price(sym, m):
        return (now["px"] if m > minute(T0) else 150.0) if sym == "SOLUSDT" else PRICES.get(sym)

    home = tmp_path / "home"
    async with serve(FakeBinance(price=price, clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.on_news(notice("b7"))
        await settle(t)
        assert t.st["positions"]["SOLUSDT"]["category"] == "listado_binance"
        now["px"] = 160.0                                        # sube: el pico
        clock.advance(T0 + 40 - clock.time())
        await t.watch_once()
        now["px"] = 130.0                                        # cae al stop de 10 %: sale
        clock.advance(T0 + 70 - clock.time())
        await t.watch_once()
        assert "SOLUSDT" not in t.st["positions"]
    entry = next(r for r in rows(home) if r["decision"] == "entrar")
    assert entry["aviso"]["demora_s"] == 1.17 and isinstance(entry["camino_ms"], int) and entry["camino_ms"] >= 0
    assert entry["params"] == {"max_late_s": 2.2, "max_rise": 0.08, "tipo": "listado_spot"} and entry["ref"]["precio_base"] == 150.0
    out = next(r for r in rows(home, "operaciones") if r.get("tipo") == "salida")
    tray = out["trayectoria"]
    assert out["motivo"] == "stop_loss" and tray["max_pct"] > 2 and tray["min_pct"] < -10
    assert "30" in tray["en_pct"] and 35 <= tray["t_max_s"] <= 45
