"""Armado por estrategia (pedido del dueño del 6-nov: "el módulo de inversión automática de upbit ... ya quede funcionando
con dinero real ... mientras seguimos haciendo pruebas ... con los demás módulos"): `armar-real --estrategias
listado_upbit` deja abrir con el operador real SOLO esa estrategia. Lo frena el operador (live.py) antes de pedir datos y,
de nuevo, el venue antes de cada orden. Salir siempre se puede. Se prueba con el simulacro local de Binance: el mismo
operador y el mismo venue que el dinero real, sin ninguna orden."""
import asyncio
import calendar
import copy
import json
from decimal import Decimal as D

import aiohttp
import pytest

from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.execution.instruments import Instrument, InstrumentRegistry
from tradingagent.execution.live import arming
from tradingagent.execution.live.binance_dryrun import LocalDryRunClient
from tradingagent.execution.live.binance_venue import BinanceVenue
from tradingagent.execution.orders import OrderIntent, OrderStatus, OrderType, Side
from tradingagent.execution.portfolio import Portfolio
from tradingagent.intelligence.classifier import Signal
from tradingagent.live import LiveTrader
from tradingagent.marketdata import binance_futures as bfd
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import minute

T0 = calendar.timegm((2026, 11, 6, 9, 0, 58))
UPBIT = "솔라나(SOL) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"
DELIST = "Binance Will Delist SOL on 2026-11-20"


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)


def news(nid, title, source, published=T0):
    it = NewsItem(id=nid, source=source, tier=1, kind="relay", title=title, body="", published_at=published,
                  received_at=published + 0.2)
    return IngestedNews(item=it, age_s=0.2, freshness="fresh", dedup=DedupResult("new", nid),
                        noise=NoiseResult(0, False), accepted=True)


def registry():
    return InstrumentRegistry([
        Instrument("SOLUSDT", "binance-futuros", "SOL", "USDT", kind="perp", tick_size=D("0.01"), step_size=D("0.01"),
                   min_qty=D("0.01"), min_notional=D("5"))])


def entry(cid, category=None, *, side=Side.BUY, reduce=False, qty=None):
    tags = {"role": "exit" if reduce else "entry", **({"category": category} if category else {})}
    return OrderIntent(client_id=cid, symbol="SOLUSDT", side=side, type=OrderType.MARKET, qty=qty,
                       notional=None if qty else D("16"), max_slippage_bps=D("50"), reduce_only=reduce,
                       ref_price=D("150"), tags=tags)


def test_el_armado_guarda_la_lista_y_solo_deja_esas():
    data = {"estrategias": ["listado_upbit"]}
    assert arming.strategy_allowed(data, "listado_upbit")
    assert not arming.strategy_allowed(data, "deslistado_corto") and not arming.strategy_allowed(data, None)
    assert arming.strategy_allowed({}, "deslistado_corto") and arming.strategy_allowed({}, None)   # sin lista: todas


def test_una_lista_vacia_no_arma_nada(tmp_path):
    with pytest.raises(ValueError):
        arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
                   strategies=(" ",))
    assert not (tmp_path / arming.ARM_FILE).exists()
    data = arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
                      strategies=("listado_upbit", "listado_upbit"))
    assert data["estrategias"] == ["listado_upbit"]
    assert "estrategias" not in arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100,
                                           venue="binance-futuros", mode="local")     # lo de siempre: todas


async def test_el_venue_solo_abre_las_estrategias_armadas_y_salir_siempre_se_puede(tmp_path):
    clock = ManualClock(T0)
    home = tmp_path / "home"
    arming.arm(home, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
               strategies=("listado_upbit",))
    async with serve(FakeBinance(price=lambda sym, m: PRICES.get(sym), clock=clock).app()) as url, \
            aiohttp.ClientSession() as s:
        books = bfd.books(s, clock, url)
        client = LocalDryRunClient(books, s, capital=100, clock=clock, public_base=url,
                                   state_path=home / "simulacro" / "exchange-simulado.json")
        v = BinanceVenue(client, s, registry(), books, Portfolio(D("100")), clock, home=home, api_key="",
                         limits=limits_mod.load(), state_path=home / "simulacro" / "stops.json", simulated=True)
        assert (await v.submit(entry("d1", "deslistado_corto", side=Side.SELL))).reason == "estrategia_no_armada"
        assert (await v.submit(entry("n1"))).reason == "estrategia_no_armada"            # sin decir cuál: no pasa
        o = await v.submit(entry("u1", "listado_upbit"))
        assert o.status is OrderStatus.FILLED
        clock.advance(6)
        out = await v.submit(entry("x1", side=Side.SELL, reduce=True, qty=o.filled_qty))   # la salida no dice
        assert out.status is OrderStatus.FILLED and v.portfolio.qty("SOLUSDT") == 0      # estrategia: sale igual


async def test_el_operador_armado_solo_para_upbit_entra_en_el_listado_y_no_en_el_deslistado(tmp_path):
    clock = ManualClock(T0 + 0.6)                                       # v3.9: el listado nuevo, hasta 1 s del mensaje
    home = tmp_path / "home"
    base = home / "simulacro" / "binance-futuros"
    arming.arm(home, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
               strategies=("listado_upbit",))
    def price(sym, m):                                                   # SOL salta en el minuto siguiente al aviso
        return (160.0 if m > minute(T0) else 150.0) if sym == "SOLUSDT" else PRICES.get(sym)

    async with serve(FakeBinance(price=price, clock=clock).app()) as url, aiohttp.ClientSession() as s:
        lim = copy.deepcopy(limits_mod.load())

        def local(reg, books, pf, clk):
            client = LocalDryRunClient(books, s, capital=100, clock=clk, public_base=url,
                                       state_path=base / "exchange-simulado.json")
            return BinanceVenue(client, s, reg, books, pf, clk, home=home, api_key="", limits=lim,
                                state_path=base / "stops.json", simulated=True)

        t = LiveTrader(BinanceFutures(base=url), s, clock, lim, alerts=Alerts(), home=home, capital=100.0,
                       venue_factory=local, base_dir=base, real=True, simulated=True)
        await t.start()
        assert t.armed_only() == frozenset({"listado_upbit"}) and t.run().only == frozenset({"listado_upbit"})
        await t.on_news(news("d1", DELIST, "binance.ws"))                  # el deslistado: ni pide datos
        assert not t.pending and not t.st["positions"]
        await t.on_news(news("u1", UPBIT, "upbit.avisos"))                 # el listado de Upbit: entra
        while t.pending:
            await asyncio.gather(*list(t.pending))
        assert t.st["positions"]["SOLUSDT"]["category"] == "listado_upbit"
        run = t.run()                                                      # cualquier otra estrategia, en el operador
        ev = {"id": "x1", "title": "x", "source": "x", "published_at": clock.time(), "received_at": clock.time()}
        await run.enter(ev, Signal("listado", 0.8, 0.7, ("ETH",), 15), "ETH", [])
        assert "estrategia_no_armada:listado" in run.decisions[-1]["motivos"]
        assert run.decisions[-1]["decision"] == "no_entrar"
    rows = [json.loads(x) for x in (base / "decisiones" / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any((r.get("signal") or {}).get("category") == "deslistado_corto" for r in rows)


def test_armar_por_estrategia_desde_la_consola(tmp_path, monkeypatch, capsys):
    from tradingagent.__main__ import main
    home = tmp_path / "home"
    monkeypatch.setenv("TRADING_HOME", str(home))
    assert main(["armar-real", "--simulacro", "--estrategias", "listado_upbit"]) == 0
    assert "solo listados de Upbit al instante" in capsys.readouterr().out
    assert json.loads((home / arming.ARM_FILE).read_text(encoding="utf-8"))["estrategias"] == ["listado_upbit"]
    assert main(["armar-real", "--simulacro", "--estrategias", "listado_upbitt"]) == 1    # un error de tipeo no arma
    assert "estrategia desconocida: listado_upbitt" in capsys.readouterr().out
    assert json.loads((home / arming.ARM_FILE).read_text(encoding="utf-8"))["estrategias"] == ["listado_upbit"]


def test_el_aviso_de_arranque_dice_si_hay_dinero_real_y_que_estrategias(tmp_path):
    from tradingagent.app import armed_summary
    assert armed_summary(tmp_path) == (False, "")
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
               strategies=("listado_upbit",))
    real, text = armed_summary(tmp_path)
    assert not real and text.startswith("🧪 Simulacro") and "solo listados de Upbit al instante" in text
    arming.arm(tmp_path, 3, "clave", arming.phrase(3), venue="binance-futuros", strategies=("listado_upbit",))
    real, text = armed_summary(tmp_path)
    assert real and "DINERO REAL" in text and "solo listados de Upbit al instante; lo demás, sin plata" in text
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local",
               file=arming.SIM_FILE)                                         # y el simulacro de los demás, al lado
    assert "lo demás, en el simulacro (plata de mentira)" in armed_summary(tmp_path)[1]
