from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import JournalSpy
from tradingagent.core.bus import EventBus
from tradingagent.execution.instruments import Instrument, InstrumentRegistry
from tradingagent.execution.orders import OrderIntent, OrderStatus, OrderType, Side, TimeInForce
from tradingagent.execution.paper import LatencyModel, PaperConfig, PaperVenue
from tradingagent.execution.portfolio import Portfolio
from tradingagent.execution.service import TOPIC_ORDERS, ExecutionService
from tradingagent.marketdata.book import OrderBook
from tradingagent.marketdata.providers import StaticBookProvider

SPOT = Instrument("BTCUSDT", "test", "BTC", "USDT", tick_size=D("0.01"), step_size=D("0.001"), min_qty=D("0.001"),
                  min_notional=D("5"), maker_fee_bps=D("0"), taker_fee_bps=D("10"))
PERP = Instrument("BTCUSDT-PERP", "test", "BTC", "USDT", kind="perp", tick_size=D("0.1"), step_size=D("0.001"),
                  min_qty=D("0.001"), min_notional=D("5"), maker_fee_bps=D("2"), taker_fee_bps=D("5"),
                  max_leverage=D("1"))


def bk(clock, bids, asks, symbol="BTCUSDT", age=0.0):
    return OrderBook.from_levels("test", symbol, bids, asks, clock.time() - age)


class Harness:
    def __init__(self, clock, cash="10000", latency_ms=100.0, participation="1", **cfg):
        self.clock = clock
        self.books = StaticBookProvider()
        self.during_latency = None                 # libro que aparece mientras la orden viaja
        self.events = []
        self.portfolio = Portfolio(D(cash))
        config = PaperConfig(latency=LatencyModel(median_ms=latency_ms, sigma=0.0), participation=D(participation),
                             **cfg)
        self.venue = PaperVenue(InstrumentRegistry((SPOT, PERP)), self.books, self.portfolio, clock, config,
                                on_event=self.events.append, sleep=self.sleep)

    async def sleep(self, s):
        self.clock.advance(s)
        if self.during_latency is not None:
            self.books.set(self.during_latency)
            self.during_latency = None

    def kinds(self):
        return [e.kind for e in self.events]


def intent(cid, side="buy", **kw):
    return OrderIntent(client_id=cid, symbol=kw.pop("symbol", "BTCUSDT"), side=Side(side), **kw)


async def test_compra_a_mercado_recorre_el_libro_con_comision_y_deslizamiento(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "1"), ("101", "1"), ("102", "5")]))
    o = await h.venue.submit(intent("c1", qty=D("1.5"), max_slippage_bps=D("300")))
    assert o.status is OrderStatus.FILLED and o.filled_qty == D("1.5")
    assert o.avg_price == (D("100") + D("0.5") * D("101")) / D("1.5")
    fee = (D("100") + D("50.5")) * D("10") / D("10000")
    assert o.fees == fee and h.portfolio.cash == D("10000") - D("150.5") - fee
    assert h.portfolio.positions["BTCUSDT"].qty == D("1.5")
    assert float(o.fills[-1].slippage_bps) > 0                      # pagó más que el mejor ask visto
    assert h.kinds() == ["accepted", "fill", "filled"]                              # un llenado al VWAP


async def test_tope_de_precio_deja_parcial_y_sin_liquidez_vence(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "1"), ("100.6", "10")]))
    o = await h.venue.submit(intent("c1", qty=D("2"), max_slippage_bps=D("50")))      # tope 100.50
    assert o.filled_qty == D("1") and o.status is OrderStatus.CANCELED and "resto" in o.reason
    h.books.set(bk(clock, [("99", "5")], [("101", "10")]))
    o2 = await h.venue.submit(intent("c2", qty=D("1"), max_slippage_bps=D("50"), ref_price=D("100")))
    assert o2.status is OrderStatus.EXPIRED and o2.filled_qty == 0                  # nada dentro del tope


async def test_market_sin_tope_recibe_el_tope_por_defecto(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "0.5"), ("200", "10")]))
    o = await h.venue.submit(intent("c1", qty=D("1")))                               # sin max_slippage_bps
    assert o.filled_qty == D("0.5")                                                  # nunca llegó a 200


async def test_la_latencia_llena_con_el_libro_de_llegada(clock):
    h = Harness(clock, latency_ms=150)
    h.books.set(bk(clock, [("99", "5")], [("100", "5")]))
    h.during_latency = bk(clock, [("100.3", "5")], [("100.4", "5")], age=-0.15)      # el precio subió en el viaje
    o = await h.venue.submit(intent("c1", qty=D("1"), max_slippage_bps=D("100")))
    assert o.avg_price == D("100.4") and o.fills[0].slippage_bps == D("40")          # 40 bps contra lo visto
    assert h.venue.summary()["latency_ms_avg"] == 150.0


async def test_participacion_parcial_de_la_profundidad(clock):
    h = Harness(clock, participation="0.5")
    h.books.set(bk(clock, [("99", "5")], [("100", "1")]))
    o = await h.venue.submit(intent("c1", qty=D("1"), max_slippage_bps=D("10")))
    assert o.filled_qty == D("0.5") and o.status is OrderStatus.CANCELED


async def test_redondeos_minimos_y_banda_de_precio(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "5")]))
    o = await h.venue.submit(intent("a", qty=D("0.0019"), max_slippage_bps=D("10")))
    assert o.qty == D("0.001")                                                       # al lote, hacia abajo
    assert (await h.venue.submit(intent("b", qty=D("0.0004")))).reason == "cantidad_menor_al_minimo"
    assert (await h.venue.submit(intent("c", notional=D("3")))).reason == "nominal_menor_al_minimo"
    assert (await h.venue.submit(intent("d", qty=D("0.01")))).reason == "nominal_menor_al_minimo"   # 1 USDT
    far = await h.venue.submit(intent("e", type=OrderType.LIMIT, tif=TimeInForce.GTC, qty=D("1"),
                                      limit_price=D("80")))
    assert far.reason == "precio_fuera_de_banda"
    lim = await h.venue.submit(intent("f", type=OrderType.LIMIT, tif=TimeInForce.GTC, qty=D("0.1"),
                                      limit_price=D("99.567")))
    assert lim.limit_price == D("99.56") and lim.status is OrderStatus.NEW          # compra: tick de abajo
    assert (await h.venue.submit(intent("g", symbol="XYZ", qty=D("1")))).reason == "instrumento_desconocido"


async def test_limite_en_reposo_solo_se_llena_si_lo_atraviesan(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99.5", "5")], [("100", "5")]))
    o = await h.venue.submit(intent("l1", type=OrderType.LIMIT, tif=TimeInForce.GTC, qty=D("1"),
                                    limit_price=D("99")))
    assert o.status is OrderStatus.NEW and h.venue.open_orders() == [o]
    await h.venue.on_book(bk(clock, [("98.5", "5")], [("99", "5")]))                # lo toca: no alcanza
    assert o.filled_qty == 0
    await h.venue.on_book(bk(clock, [("98", "5")], [("98.9", "0.4"), ("99.5", "9")]))  # lo atraviesa
    assert o.filled_qty == D("0.4") and o.status is OrderStatus.PARTIALLY_FILLED
    assert o.fills[0].price == D("99") and o.fills[0].liquidity == "maker" and o.fills[0].fee == 0
    await h.venue.on_book(bk(clock, [("97", "5")], [("98", "5")]))
    assert o.status is OrderStatus.FILLED and o.filled_qty == D("1")


async def test_limite_que_cruza_al_llegar_es_taker_y_el_resto_queda(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "0.3"), ("101", "5")]))
    o = await h.venue.submit(intent("l1", type=OrderType.LIMIT, tif=TimeInForce.GTC, qty=D("1"),
                                    limit_price=D("100")))
    assert o.filled_qty == D("0.3") and o.fills[0].liquidity == "taker" and o in h.venue.open_orders()


async def test_stop_loss_se_dispara_y_se_reprecia_hasta_salir(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "5")]))
    await h.venue.submit(intent("in", qty=D("1"), max_slippage_bps=D("10")))
    sl = await h.venue.submit(intent("sl", side="sell", type=OrderType.STOP_MARKET, tif=TimeInForce.GTC,
                                     qty=D("1"), stop_price=D("95"), reduce_only=True,
                                     tags={"role": "stop_loss"}))
    assert sl.status is OrderStatus.NEW and not sl.triggered
    await h.venue.on_book(bk(clock, [("96", "5")], [("96.5", "5")]))
    assert not sl.triggered
    await h.venue.on_book(bk(clock, [("94.9", "0.3"), ("90", "10")], [("95.2", "5")]))  # hueco: dispara
    assert sl.triggered and sl.filled_qty == D("0.3") and sl.status is OrderStatus.PARTIALLY_FILLED
    await h.venue.on_book(bk(clock, [("93", "10")], [("93.5", "5")]))                # re-precia y sale
    assert sl.status is OrderStatus.FILLED and h.portfolio.qty("BTCUSDT") == 0
    assert sum(f.realized_pnl for f in sl.fills) == D("0.3") * (D("94.9") - D("100")) + D("0.7") * (D("93") - D("100"))
    assert "triggered" in h.kinds()


async def test_oco_parcial_reduce_al_hermano_y_completo_lo_cancela(clock):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "5")]))
    await h.venue.submit(intent("in", qty=D("1"), max_slippage_bps=D("10")))
    common = dict(side="sell", tif=TimeInForce.GTC, qty=D("1"), reduce_only=True, oco_group="g1")
    tp = await h.venue.submit(intent("tp", type=OrderType.LIMIT, limit_price=D("103"), tags={"role": "take_profit"},
                                     **common))
    sl = await h.venue.submit(intent("sl", type=OrderType.STOP_MARKET, stop_price=D("95"), tags={"role": "stop_loss"},
                                     **common))
    await h.venue.on_book(bk(clock, [("103.5", "0.4")], [("104", "5")]))               # TP parcial
    assert tp.filled_qty == D("0.4") and sl.qty == D("0.6")
    await h.venue.on_book(bk(clock, [("94", "10")], [("94.5", "5")]))                # SL sale con lo que queda
    assert sl.status is OrderStatus.FILLED and sl.filled_qty == D("0.6")
    assert tp.status is OrderStatus.CANCELED and h.portfolio.qty("BTCUSDT") == 0


async def test_idempotencia_libro_viejo_saldo_y_spot_sin_corto(clock):
    h = Harness(clock, cash="100")
    h.books.set(bk(clock, [("99", "5")], [("100", "5")]))
    a = await h.venue.submit(intent("x", qty=D("0.5"), max_slippage_bps=D("10")))
    b = await h.venue.submit(intent("x", qty=D("0.5"), max_slippage_bps=D("10")))
    assert a is b and len(a.fills) == 1                                              # mismo id: una sola vez
    assert (await h.venue.submit(intent("y", qty=D("1"), max_slippage_bps=D("10")))).reason == "saldo_insuficiente"
    assert (await h.venue.submit(intent("z", side="sell", qty=D("1"), max_slippage_bps=D("10")))).reason \
        == "posicion_insuficiente_spot"
    h.books.set(bk(clock, [("99", "5")], [("100", "5")], age=10))
    assert (await h.venue.submit(intent("w", qty=D("0.1")))).reason.startswith("sin_datos")
    h.books.set(bk(clock, [("59990", "5")], [("60000", "5")], symbol="BTCUSDT-PERP"))
    ro = await h.venue.submit(intent("r", side="buy", symbol="BTCUSDT-PERP", qty=D("0.01"), reduce_only=True,
                                     max_slippage_bps=D("10")))
    assert ro.status is OrderStatus.CANCELED and ro.reason == "reduce_only_sin_posicion"


async def test_perp_corto_pnl_y_margen(clock):
    h = Harness(clock, cash="1000")
    h.books.set(bk(clock, [("59990", "5")], [("60000", "5")], symbol="BTCUSDT-PERP"))
    s = await h.venue.submit(intent("s", side="sell", symbol="BTCUSDT-PERP", qty=D("0.01"), max_slippage_bps=D("10")))
    assert s.status is OrderStatus.FILLED and h.portfolio.qty("BTCUSDT-PERP") == D("-0.01")
    big = await h.venue.submit(intent("b", side="sell", symbol="BTCUSDT-PERP", qty=D("0.02"), max_slippage_bps=D("10")))
    assert big.reason == "margen_insuficiente"                                       # 1x: 1.800 > 1.000
    h.books.set(bk(clock, [("58990", "5")], [("59000", "5")], symbol="BTCUSDT-PERP"))
    c = await h.venue.submit(intent("c", side="buy", symbol="BTCUSDT-PERP", qty=D("0.01"), reduce_only=True,
                                    max_slippage_bps=D("10")))
    assert c.fills[0].realized_pnl == D("0.01") * (D("59990") - D("59000"))
    fees = s.fees + c.fees
    assert h.portfolio.cash == D("1000") + D("9.90") - fees and h.portfolio.qty("BTCUSDT-PERP") == 0


@settings(max_examples=150, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["buy", "sell"]), st.integers(1, 50), st.integers(90, 110)), min_size=1,
                max_size=30))
def test_contabilidad_perp_conserva_valor(trades):
    """Sin comisiones: equity = caja inicial + Σ PnL de cada trade marcado al último precio."""
    p = Portfolio(D("100000"))
    flows = D(0)
    last = None
    for side, q, px in trades:
        qty, price = D(q) / 10, D(px)
        p.apply_fill(PERP, Side(side), qty, price, D(0))
        flows += qty * price * (-1 if side == "buy" else 1)
        last = price
    pos = p.qty(PERP.symbol)
    # El precio promedio puede ser periódico (90,666…): Decimal tiene 28 dígitos, error ~1e-22 USDT.
    assert abs(p.equity({PERP.symbol: last}) - (D("100000") + flows + pos * last)) < D("1e-12")


def test_spot_rechaza_corto_y_flujo_de_caja_exacto():
    p = Portfolio(D("1000"))
    p.apply_fill(SPOT, Side.BUY, D("2"), D("100"), D("0.2"))
    p.apply_fill(SPOT, Side.SELL, D("1"), D("110"), D("0.11"))
    assert p.cash == D("1000") - D("200.2") + D("109.89") and p.positions["BTCUSDT"].realized == D("10")
    with pytest.raises(ValueError):
        p.apply_fill(SPOT, Side.SELL, D("2"), D("110"), D("0"))


async def test_servicio_kill_tope_por_minuto_bus_diario_y_alertas(clock, tmp_path):
    h = Harness(clock)
    h.books.set(bk(clock, [("99", "5")], [("100", "5")]))
    bus, j = EventBus(), JournalSpy()
    sub = bus.subscribe(TOPIC_ORDERS, "t", maxsize=100)

    class AlertsSpy:
        def __init__(self):
            self.sent = []

        def submit(self, a):
            self.sent.append(a)
            return True

    alerts = AlertsSpy()
    kill = tmp_path / "KILL"
    svc = ExecutionService(h.venue, bus, clock, kill_file=kill, journal=j, alerts=alerts, max_orders_per_min=3)
    ok, _ = svc.submit(intent("e1", qty=D("0.1"), max_slippage_bps=D("10"),
                              tags={"role": "entry", "why": "reestructuración de deuda", "impact": 0.8}))
    assert ok
    await svc._execute(svc.queue.get_nowait())
    kill.write_text("")
    assert svc.submit(intent("e2", qty=D("0.1")))[1] == "interruptor_de_emergencia"
    ok, _ = svc.submit(intent("x1", side="sell", qty=D("0.1"), reduce_only=True, max_slippage_bps=D("10"),
                              tags={"role": "stop_loss"}))
    assert ok                                                                      # salir siempre se puede
    await svc._execute(svc.queue.get_nowait())
    kill.unlink()
    assert svc.submit(intent("e3", qty=D("0.1")))[0]
    assert svc.submit(intent("e4", qty=D("0.1")))[1] == "tope_de_ordenes_por_minuto"
    assert sub.queue.qsize() >= 4 and any(k == "order" for k, _ in j.records)
    kinds = [a.kind for a in alerts.sent]
    assert kinds == ["trade", "stop_loss"]
    assert "LONG" in alerts.sent[0].text and "reestructuración" in alerts.sent[0].text and "+0.80" in alerts.sent[0].text
