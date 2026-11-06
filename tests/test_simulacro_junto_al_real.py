"""El simulacro JUNTO al dinero real (pedido del dueño del 6-nov: la práctica sobra, el simulacro mide mejor, "opción 1
siempre que los simulacros de los demás módulos no compitan o afecten en nada a los módulos que trabajen con dinero
real"): su propio archivo de armado (SIMULACRO.json, que nunca arma plata), nunca opera lo que opera el dinero real, el
dinero real tiene sus propias conexiones y no lee las noticias que no puede operar, y la práctica se puede apagar."""
import json
import time
from decimal import Decimal as D

import aiohttp
import pytest

from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import config, dashboard, paths
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.execution.instruments import Instrument, InstrumentRegistry
from tradingagent.execution.live import arming
from tradingagent.execution.live.binance_dryrun import LocalDryRunClient
from tradingagent.execution.live.binance_venue import BinanceVenue
from tradingagent.execution.orders import OrderIntent, OrderStatus, OrderType, Side
from tradingagent.execution.portfolio import Portfolio
from tradingagent.live import LiveTrader
from tradingagent.marketdata import binance_futures as bfd
from tradingagent.research.futures import BinanceFutures

KEY, SECRET = "clave-trading-de-prueba-0000", "secreto-trading-de-prueba-0000"


def arm_real(home, strategies=("listado_upbit",)):
    return arming.arm(home, 3, KEY, arming.phrase(3), venue="binance-futuros", strategies=strategies)


def arm_side(home):
    return arming.arm(home, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
                      file=arming.SIM_FILE)


def test_el_archivo_del_simulacro_nunca_arma_plata(tmp_path):
    with pytest.raises(ValueError):                                   # ni con la frase
        arming.arm(tmp_path, 3, KEY, arming.phrase(3), venue="binance-futuros", file=arming.SIM_FILE)
    with pytest.raises(ValueError):                                   # ni contra la demo (órdenes de verdad)
        arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="demo",
                   file=arming.SIM_FILE)
    assert not (tmp_path / arming.SIM_FILE).exists()
    paths.write_json_atomic(tmp_path / arming.SIM_FILE, {**arm_real(tmp_path)})     # escrito a mano: igual no arma
    assert arming.status(tmp_path, KEY, file=arming.SIM_FILE)[:2] == (False, "no_es_simulacro")
    arm_side(tmp_path)
    assert arming.status(tmp_path, None, venue="binance-futuros", file=arming.SIM_FILE)[0]
    assert arming.disarm(tmp_path, arming.SIM_FILE) and (tmp_path / arming.ARM_FILE).exists()   # el real, intacto


def test_armar_el_simulacro_junto_al_real_desde_la_consola(tmp_path, monkeypatch, capsys):
    from tradingagent.__main__ import main
    home = tmp_path / "home"
    monkeypatch.setenv("TRADING_HOME", str(home))
    real = arm_real(home)
    assert main(["armar-real", "--simulacro", "--demo"]) == 1                    # junto al real, solo el local
    assert main(["armar-real", "--simulacro"]) == 0
    out = capsys.readouterr().out
    assert "junto al dinero real" in out and "Nunca opera lo que opera el dinero real" in out
    assert "listados de Upbit al instante" in out
    side = json.loads((home / arming.SIM_FILE).read_text(encoding="utf-8"))
    assert side["simulacro"] and side["modo"] == "local" and side["capital_usdt"] == 100
    assert json.loads((home / arming.ARM_FILE).read_text(encoding="utf-8")) == real          # el real, igual
    assert main(["armar-real", "--desarmar", "--simulacro"]) == 0
    assert not (home / arming.SIM_FILE).exists() and (home / arming.ARM_FILE).exists()
    arm_real(home, strategies=None)                                   # el real con TODAS: no queda nada para simular
    assert main(["armar-real", "--simulacro"]) == 1 and not (home / arming.SIM_FILE).exists()


def test_el_simulacro_suelta_lo_que_el_dueno_arma_con_plata(tmp_path):
    t = LiveTrader(BinanceFutures(), None, ManualClock(time.time()), limits_mod.load(), home=tmp_path, real=True,
                   simulated=True, arm_file=arming.SIM_FILE, exclude_real=True)
    arm_side(tmp_path)
    assert t.excluded() == frozenset() and t.allows("listado_upbit")         # sin dinero real: todo
    arm_real(tmp_path)
    assert t.excluded() == {"listado_upbit"} and not t.allows("listado_upbit") and t.allows("deslistado_corto")
    time.sleep(0.02)
    arm_real(tmp_path, strategies=("listado_upbit", "deslistado_corto"))   # el dueño suma una: el simulacro la suelta
    assert not t.allows("deslistado_corto") and t.allows("resultados_impulso")
    time.sleep(0.02)
    arm_real(tmp_path, strategies=None)                                # todo con plata: el simulacro no abre nada
    assert t.excluded() == {"*"} and not t.allows("resultados_impulso")


async def test_el_venue_del_simulacro_usa_su_archivo_y_no_el_del_real(tmp_path):
    clock = ManualClock(1793700000)
    home = tmp_path / "home"
    arm_real(home)

    def intent(cid):
        return OrderIntent(client_id=cid, symbol="SOLUSDT", side=Side.BUY, type=OrderType.MARKET, notional=D("16"),
                           max_slippage_bps=D("50"), ref_price=D("150"), tags={"role": "entry", "category": "listado"})

    reg = InstrumentRegistry([Instrument("SOLUSDT", "binance-futuros", "SOL", "USDT", kind="perp",
                                         tick_size=D("0.01"), step_size=D("0.01"), min_qty=D("0.01"),
                                         min_notional=D("5"))])
    async with serve(FakeBinance(price=lambda sym, m: PRICES.get(sym), clock=clock).app()) as url, \
            aiohttp.ClientSession() as s:
        books = bfd.books(s, clock, url)
        v = BinanceVenue(LocalDryRunClient(books, s, capital=100, clock=clock, public_base=url,
                                           state_path=home / "simulacro" / "exchange-simulado.json"),
                         s, reg, books, Portfolio(D("100")), clock, home=home, api_key="", limits=limits_mod.load(),
                         state_path=home / "simulacro" / "stops.json", simulated=True, arm_file=arming.SIM_FILE)
        assert (await v.submit(intent("a"))).reason == "real_no_armado:no_armado"      # sin su archivo, nada
        arm_side(home)
        assert (await v.submit(intent("b"))).status is OrderStatus.FILLED


async def test_con_dinero_real_solo_para_upbit_no_lee_las_demas_noticias(tmp_path):
    class Llm:
        enabled, calls = True, 0

        async def classify(self, *a):
            Llm.calls += 1

    arm_real(tmp_path)
    t = LiveTrader(BinanceFutures(), None, ManualClock(time.time()), limits_mod.load(), home=tmp_path, real=True,
                   llm=Llm())
    t.st = {"positions": {}}
    it = NewsItem(id="n1", source="coindesk", tier=2, kind="rss", title="Bitcoin ETF approved by the SEC", body="",
                  published_at=time.time(), received_at=time.time())
    await t.on_news(IngestedNews(item=it, age_s=1, freshness="fresh", dedup=DedupResult("new", "n1"),
                                 noise=NoiseResult(0, False), accepted=True))
    assert Llm.calls == 0 and not t.pending                             # ni IA, ni candado, ni datos


async def test_el_agente_corre_el_real_y_el_simulacro_sin_practica(tmp_path, monkeypatch):
    from tradingagent.app import Agent

    async def started(self):
        self.started_ok = True

    monkeypatch.setattr(LiveTrader, "start", started)
    monkeypatch.setenv("TRADING_HOME", str(tmp_path))
    arm_real(tmp_path)
    arm_side(tmp_path)
    cfg = config.load(None)
    cfg["trader"]["practica"] = False
    env = {"BINANCE_API_KEY": KEY, "BINANCE_API_SECRET": SECRET, "ANTHROPIC_API_KEY": "sk-ant-prueba-0000"}
    agent = Agent(cfg, env)
    agent.alerts = agent.journal = None
    async with aiohttp.ClientSession() as s, aiohttp.ClientSession() as rs:
        await agent._start_traders(s, rs, limits_mod.load())
        real, sim = agent.real_trader, agent.sim_trader
        assert agent.trader is None and real is not None and sim is not None
        assert not real.simulated and real.session is rs and sim.session is s       # el real, con sus conexiones
        assert sim.exclude_real and sim.arm_file == arming.SIM_FILE and sim.excluded() == {"listado_upbit"}
        assert real.llm is sim.llm is agent.llm and agent.llm is not None            # una sola IA
        assert sim.owns_llm_budget and not real.owns_llm_budget                     # el tope lo lleva el simulacro
        assert agent.traders() == [real, sim]                                        # el real recibe primero
        assert agent._on_binance(rs) is real and agent._on_binance(s) is sim        # conexiones tibias en los dos
        v = sim.venue_factory(None, None, None, ManualClock(0))
        assert v.arm_file == arming.SIM_FILE and v.simulated and v._key == ""        # nunca la clave de trading


def test_el_tablero_muestra_el_real_y_el_simulacro_y_la_practica_apagada(tmp_path):
    arm_real(tmp_path)
    arm_side(tmp_path)
    for folder in ("real", "simulacro", "paper-vivo"):
        paths.write_json_atomic(tmp_path / folder / "binance-futuros" / "estado.json",
                                {"initial": "100", "cash": "100", "mercado": "binance-futuros", "updated_at": 1})
    hb = {"ts": time.time(), "trader": None, "real": {"mercado": "binance-futuros"}}
    active, old = dashboard.split_accounts(tmp_path, hb, time.time())
    assert sorted(a["tipo"] for a in active) == ["real", "simulacro"]
    assert [a["tipo"] for a in old] == ["practica"]                             # la práctica, apagada
