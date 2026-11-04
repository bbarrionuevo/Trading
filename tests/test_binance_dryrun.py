"""Simulacro LOCAL de Binance (execution/live/binance_dryrun.py): el operador real completo sobre el libro de
producción, con órdenes simuladas en la PC. Lo más importante: en todo el ciclo (entrada, stop, salida, conciliación)
Binance no recibe NI UN pedido que no sea de lectura, y la clave de trading no se usa nunca."""
import calendar
import json
import time
from decimal import Decimal as D

import aiohttp

from conftest import serve
from fake_binance import KEY, PRICES, SECRET, FakeBinance
from tradingagent import config
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.execution.instruments import Instrument, InstrumentRegistry
from tradingagent.execution.live import arming
from tradingagent.execution.live.binance_dryrun import LocalDryRunClient
from tradingagent.execution.live.binance_futures import BASE, BinanceClient
from tradingagent.execution.live.binance_venue import BinanceVenue
from tradingagent.execution.orders import EXCHANGE_FLAT, OrderIntent, OrderStatus, OrderType, Side
from tradingagent.execution.portfolio import Portfolio
from tradingagent.marketdata import binance_futures as bfd
from tradingagent.research.calibracion import render, report

T0 = calendar.timegm((2026, 11, 4, 12, 0, 0))


def registry():
    return InstrumentRegistry([
        Instrument("SOLUSDT", "binance-futuros", "SOL", "USDT", kind="perp", tick_size=D("0.01"), step_size=D("0.01"),
                   min_qty=D("0.01"), min_notional=D("5"))])


def intent(cid, side, *, notional=None, qty=None, reduce=False, emergency_stop=None):
    tags = {"role": "exit" if reduce else "entry", **({"emergency_stop": emergency_stop} if emergency_stop else {})}
    return OrderIntent(client_id=cid, symbol="SOLUSDT", side=side, type=OrderType.MARKET, qty=qty, notional=notional,
                       max_slippage_bps=D("50"), reduce_only=reduce, ref_price=D("150"), tags=tags)


def sim(home):
    arming.arm(home, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", mode="local")


def local_venue(s, url, clock, home, *, reader=True):
    books = bfd.books(s, clock, url)
    client = LocalDryRunClient(books, s, capital=100, clock=clock, public_base=url,
                               reader=BinanceClient(s, KEY, SECRET, base=url) if reader else None,
                               state_path=home / "simulacro" / "exchange-simulado.json")
    return BinanceVenue(client, s, registry(), books, Portfolio(D("100")), clock, home=home, api_key="",
                        limits=limits_mod.load(), state_path=home / "simulacro" / "stops.json", simulated=True)


async def test_ejecuta_contra_el_libro_real_y_binance_no_recibe_ninguna_orden(tmp_path):
    clock = ManualClock(T0)
    prices = dict(PRICES)
    fake = FakeBinance(price=lambda sym, m: prices.get(sym), clock=clock)
    home = tmp_path / "home"
    sim(home)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        v = local_venue(s, url, clock, home)
        o = await v.submit(intent("e1", Side.BUY, notional=D("16"), emergency_stop="127.50"))
        assert o.status is OrderStatus.FILLED and o.filled_qty == D("0.1") and o.avg_price == D("150.02")  # el ask
        assert o.fees == D("0.1") * D("150.02") * D("0.0004")                    # la comisión de la cuenta
        stop = (await v.client.open_stops("SOLUSDT"))[0]
        assert stop["closePosition"] and stop["triggerPrice"] == "127.50" and await v.reconcile() == ""
        prices["SOLUSDT"] = 151.0
        clock.advance(1)                                                             # el libro nuevo
        out = await v.submit(intent("x1", Side.SELL, qty=D("0.1"), reduce=True))
        assert out.status is OrderStatus.FILLED and v.portfolio.qty("SOLUSDT") == 0
        assert v.portfolio.realized_total > 0 and await v.client.open_stops("SOLUSDT") == []
        assert (await v.client.stop(stop["clientAlgoId"]))["algoStatus"] == "EXPIRED"          # como Binance
        capped = await v.client.submit(symbol="SOLUSDT", side="BUY", qty="0.1", price="140", tif="IOC",
                                       client_id="c1")
        assert capped["status"] == "EXPIRED" and capped["executedQty"] == "0"              # el tope se respeta
    assert fake.calls and {m for m, _ in fake.calls} == {"GET"}                      # NI UNA orden a Binance
    assert not any("/order" in p or "algo" in p.lower() for _, p in fake.calls)
    rows = [json.loads(x) for x in (home / "simulacro" / "ordenes-simuladas.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [r["tipo"] for r in rows] == ["orden", "orden", "orden"]
    assert rows[0]["precio"] == "150.02" and rows[0]["lado"] == "BUY" and D(rows[1]["resultado"]) > 0
    assert rows[0]["libro_ms"] >= 0 and rows[0]["mejor"] == "150.02"


async def test_el_stop_de_emergencia_se_simula_y_el_estado_sobrevive_al_reinicio(tmp_path):
    clock = ManualClock(T0)
    prices = dict(PRICES)
    fake = FakeBinance(price=lambda sym, m: prices.get(sym), clock=clock)
    home = tmp_path / "home"
    sim(home)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        v = local_venue(s, url, clock, home, reader=False)
        await v.submit(intent("e1", Side.BUY, notional=D("16"), emergency_stop="127.50"))
        again = local_venue(s, url, clock, home, reader=False)                       # reinicio de la PC
        assert (await again.client.positions())[0]["positionAmt"] == "0.10"
        assert len(await again.client.open_stops("SOLUSDT")) == 1
        prices["SOLUSDT"] = 120.0                                                    # cruzó el disparo
        clock.advance(2)
        assert await again.client.positions() == []                                  # el exchange simulado cerró
        out = await v.submit(intent("x1", Side.SELL, qty=D("0.1"), reduce=True))
        assert out.reason == EXCHANGE_FLAT                                           # el motor se sincroniza
        bal = (await again.client.usdt())["equity"]
        assert bal < 100 - 16 * 0.15                                                 # perdió ~20 % de lo invertido
    rows = [json.loads(x) for x in (home / "simulacro" / "ordenes-simuladas.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert rows[-1]["tipo"] == "stop_de_emergencia" and D(rows[-1]["resultado"]) < 0
    assert {m for m, _ in fake.calls} == {"GET"}


def test_el_agente_arma_el_simulacro_local_sin_la_clave_de_trading(tmp_path, monkeypatch, capsys):
    from tradingagent.__main__ import main
    from tradingagent.app import Agent

    home = tmp_path / "home"
    monkeypatch.setenv("TRADING_HOME", str(home))
    assert main(["armar-real", "--simulacro", "--capital", "100", "--dias", "3"]) == 0
    assert "libro real de Binance y órdenes simuladas" in capsys.readouterr().out
    data = json.loads((home / "ARMADO.json").read_text(encoding="utf-8"))
    assert data["modo"] == "local" and data["simulacro"] and data["venues"] == ["binance-futuros"]
    env = {"BINANCE_LECTURA_KEY": "clave-lectura-0000", "BINANCE_LECTURA_SECRET": "secreto-lectura-0000",
           "BINANCE_API_KEY": "clave-trading-NO-SE-USA", "BINANCE_API_SECRET": "secreto-trading-NO-SE-USA"}
    agent = Agent(config.load(None), env)
    agent.alerts = agent.journal = None
    t = agent._build_real_trader(None, limits_mod.load())
    assert t.simulated and t.real and t.market.label == "SIMULACRO futuros Binance" and t.capital == 100
    assert t.base == home / "simulacro" / "binance-futuros"
    v = t.venue_factory(registry(), bfd.books(None, ManualClock(T0)), Portfolio(D("100")), ManualClock(T0))
    assert isinstance(v.client, LocalDryRunClient) and v._key == "" and v.simulated
    assert v.client.reader._key == "clave-lectura-0000" and v.client.reader.base == BASE   # solo para leer
    assert "NO-SE-USA" not in json.dumps({k: str(x) for k, x in vars(v).items()})            # nunca la de trading
    assert main(["armar-real", "--simulacro", "--demo"]) == 2                     # la demo pide sus claves
    monkeypatch.setenv("BINANCE_API_KEY_TEST", "clave-demo-0000")
    monkeypatch.setenv("BINANCE_API_SECRET_TEST", "secreto-demo-0000")
    assert main(["armar-real", "--simulacro", "--demo", "--capital", "100"]) == 0
    assert json.loads((home / "ARMADO.json").read_text(encoding="utf-8"))["modo"] == "demo"


def test_informe_de_calibracion(tmp_path):
    base, journal = tmp_path / "simulacro" / "binance-futuros", tmp_path / "journal"
    (base / "operaciones").mkdir(parents=True)
    journal.mkdir()
    news = [{"kind": "news", "data": {"id": "a", "source": "binance.ws", "published_at": 100.0, "received_at": 100.8,
                                      "accepted": True}},
            {"kind": "news", "data": {"id": "b", "source": "upbit.avisos", "published_at": 200.0, "received_at": 205.0,
                                      "accepted": True}}]
    (journal / "journal-20261104.jsonl").write_text("\n".join(json.dumps(r) for r in news), encoding="utf-8")
    trades = [{"tipo": "entrada", "event_id": "a", "symbol": "FOOUSDT", "ts": 104.0, "category": "listado"},
              {"tipo": "salida", "event_id": "a", "symbol": "FOOUSDT", "ts": 1000.0, "category": "listado",
               "pnl_neto": "1.50", "motivo": "expiracion"},
              {"tipo": "entrada", "event_id": "b", "symbol": "BARUSDT", "ts": 213.0, "category": "listado"},
              {"tipo": "salida", "event_id": "b", "symbol": "BARUSDT", "ts": 900.0, "category": "listado",
               "pnl_neto": "-0.50", "motivo": "stop_loss"}]
    (base / "operaciones" / "2026-11.jsonl").write_text("\n".join(json.dumps(r) for r in trades), encoding="utf-8")
    (base / "ordenes-simuladas.jsonl").write_text(json.dumps(
        {"ts": 104.0, "tipo": "orden", "lado": "BUY", "ejecutado": "1", "precio": "10.01", "mejor": "10.00",
         "libro_ms": 300}), encoding="utf-8")
    (base / "estado.json").write_text(json.dumps({"initial": "100", "equity": "101", "positions": {}}), encoding="utf-8")
    rep = report(base, journal)
    assert rep["cerradas"] == 2 and rep["ganadas"] == 1 and rep["acierto"] == 0.5 and D(rep["pnl_neto"]) == 1
    assert rep["reaccion_s"]["mediana"] == 5.6 and rep["publicada_a_entrada_s"]["mediana"] == 8.5
    assert rep["llegada_s"]["binance.ws"]["mediana"] == 0.8 and rep["deslizamiento_bps"]["mediana"] == 10.0
    text = render(rep)
    assert "acierto 50 %" in text and "+1.0000 USDT" in text and "binance.ws" in text and "+1.00 %" in text
    assert time.time() > 0
