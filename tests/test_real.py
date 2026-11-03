"""Modo REAL (execution/live/) contra un MEXC falso con órdenes: la firma y la lista cerrada de operaciones, la
prueba sin riesgo (que detecta un exchange que entiende otro lado), el armado del dueño con frase exacta,
tope y vencimiento (A-2, F-4), y el venue real: entra con tope de precio y stop de emergencia, sale siempre,
respeta el capital armado y se frena si la conciliación no cierra."""
import calendar
import copy
import time
from decimal import Decimal as D

import aiohttp
import pytest

from conftest import serve
from fake_mexc import ACCESS, SECRET
from fake_mexc_trade import FakeMexcTrade
from tradingagent import limits as limits_mod
from tradingagent import paths
from tradingagent.core.clock import ManualClock
from tradingagent.execution.instruments import Instrument, InstrumentRegistry
from tradingagent.execution.live import arming
from tradingagent.execution.live.check import real_check
from tradingagent.execution.live.mexc_futures import CLOSE_LONG, MARKET, OPEN_LONG, POST_ONLY, TradingClient, allowed
from tradingagent.execution.live.venue import RealVenue
from tradingagent.execution.orders import OrderIntent, OrderStatus, OrderType, Side
from tradingagent.execution.portfolio import Portfolio
from tradingagent.marketdata.mexc import ContractBooks

T0 = calendar.timegm((2026, 11, 3, 12, 0, 0))
PRICES = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}


def market(clock):
    return FakeMexcTrade(lambda sym, m: PRICES[sym], clock)


def armed(home, lim, *, verified=True, capital=20.0, days=7, now=None):
    arming.arm(home, capital, days, ACCESS, lim, arming.phrase(capital, days), now=now)
    if verified:
        arming.save_verification(home, {"ok": True, "simbolo": "BTC_USDT"}, ACCESS, now=now)


async def test_firma_y_lista_cerrada_de_operaciones():
    clock = ManualClock(T0)
    fake = market(clock)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        assert await c.positions("BTC_USDT") == [] and (await c.usdt())["available"] == 20.0
        oid = await c.submit(symbol="BTC_USDT", side=OPEN_LONG, vol=1, price=90_000, leverage=1, order_type=POST_ONLY,
                             external_oid="t1", stop_loss=88_000)
        assert (await c.order(oid))["stopLossPrice"] == 88_000.0
        await c.cancel([oid])
        for method, path in (("POST", "/api/v1/private/withdraw/apply"), ("GET", "/api/v1/private/account/transfer"),
                             ("POST", "/api/v1/private/account/transfer"), ("GET", "/api/v1/private/order/submit"),
                             ("POST", "/api/v1/private/planorder/place"), ("DELETE", "/api/v1/private/order/cancel")):
            with pytest.raises(PermissionError):
                await c.request(method, path, body={})
        with pytest.raises(ValueError):                                   # nunca a mercado sin tope (D-2)
            await c.submit(symbol="BTC_USDT", side=OPEN_LONG, vol=1, price=1, leverage=1, order_type=MARKET,
                           external_oid="x")
    assert fake.signed and all(x["ok"] for x in fake.signed)               # GET y POST bien firmados
    assert not any("withdraw" in p or "transfer" in p for _, p in fake.calls)   # ni siquiera salió a la red
    assert allowed("GET", "/api/v1/private/order/get/123") and not allowed("GET", "/api/v1/private/order/get/1/../x")


async def test_prueba_sin_riesgo_y_detecta_un_exchange_que_entiende_otro_lado():
    clock = ManualClock(T0)
    fake = market(clock)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        good = await real_check(c, s, base=url, pause_s=0)
        fake.swap_sides = True
        bad = await real_check(c, s, base=url, pause_s=0)
    assert good["ok"] and len(good["pasos"]) == 4, good
    assert not bad["ok"], bad                                             # al revés: la "solo maker" cruzaría, el
    assert any("lado" in x["detalle"] or "post only" in x["detalle"] for x in bad["pasos"])   # exchange la rechaza
    assert all(o["state"] == 4 for o in fake.orders.values())               # nada quedó colgado
    assert all(p["holdVol"] == 0 for p in fake.positions.values())          # ni una posición


def test_armado_frase_exacta_tope_vencimiento_y_verificacion(tmp_path):
    lim = limits_mod.load()
    home = tmp_path / "home"
    with pytest.raises(ValueError, match="frase"):
        arming.arm(home, 20, 7, ACCESS, lim, "dale")
    with pytest.raises(ValueError, match="20"):
        arming.arm(home, 25, 7, ACCESS, lim, arming.phrase(25, 7))            # calibración: hasta 20 USDT (F-4)
    with pytest.raises(ValueError, match="7"):
        arming.arm(home, 20, 8, ACCESS, lim, arming.phrase(20, 8))            # y 7 días
    assert arming.status(home, ACCESS, lim)[1] == "no_armado"
    arming.arm(home, 20, 7, ACCESS, lim, "ARMO DINERO REAL 20 USDT POR 7 DIAS", now=T0)
    assert arming.status(home, ACCESS, lim, now=T0 + 60)[1] == "sin_verificar"
    arming.save_verification(home, {"ok": True}, ACCESS, now=T0)
    assert arming.status(home, ACCESS, lim, now=T0 + 60) == (True, "", paths.read_json(home / "ARMADO.json", {}))
    assert arming.status(home, "otra-clave", lim, now=T0 + 60)[1] == "clave_distinta"
    assert arming.status(home, ACCESS, lim, now=T0 + 7 * 86400)[1] == "vencido"
    (home / "KILL").write_text("")
    assert arming.status(home, ACCESS, lim, now=T0 + 60)[1] == "kill"
    (home / "KILL").unlink()
    assert arming.disarm(home) and arming.status(home, ACCESS, lim)[1] == "no_armado"


def _intent(cid, side, *, notional=None, qty=None, reduce=False, **tags):
    return OrderIntent(client_id=cid, symbol="BTC_USDT", side=side, type=OrderType.MARKET, notional=notional, qty=qty,
                       max_slippage_bps=D("30"), reduce_only=reduce, ref_price=D("100000"), tags=tags)


async def test_venue_real_entra_con_stop_de_emergencia_sale_siempre_y_respeta_el_capital(tmp_path):
    lim = copy.deepcopy(limits_mod.load())
    clock = ManualClock(T0)
    fake = market(clock)
    home = tmp_path / "home"
    reg = InstrumentRegistry([Instrument("BTC_USDT", "mexc-futuros", "BTC", "USDT", kind="perp", tick_size=D("0.1"),
                                         step_size=D("0.0001"), min_qty=D("0.0001"), min_notional=D("1"))])
    pf = Portfolio(D("20"))
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        books = ContractBooks(s, clock, {"BTC_USDT": D("0.0001")}, base=url)
        c = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        v = RealVenue(c, s, reg, books, pf, clock, home=home, access_key=ACCESS, limits=lim, base=url)
        o = await v.submit(_intent("e0", Side.BUY, notional=D("10")))
        assert o.status is OrderStatus.REJECTED and o.reason == "real_no_armado:no_armado"   # sin armado, nada
        armed(home, lim, now=time.time())
        big = await v.submit(_intent("e1", Side.BUY, notional=D("100")))
        assert big.status is OrderStatus.REJECTED and big.reason == "supera_el_capital_armado"
        o = await v.submit(_intent("e2", Side.BUY, notional=D("10.5"), emergency_stop="98500"))
        assert o.status is OrderStatus.FILLED and o.filled_qty == D("0.0001") and o.fees > 0
        sent = fake.orders[o.order_id]
        assert sent["orderType"] == 3 and sent["openType"] == 1 and sent["side"] == OPEN_LONG   # IOC, aislado
        assert sent["stopLossPrice"] == 98_500.0 and D(str(sent["price"])) > D("100000")     # tope de precio
        assert pf.qty("BTC_USDT") == D("0.0001") and await v.reconcile() == ""
        arming.disarm(home)                                                     # revocado: no entra más...
        again = await v.submit(_intent("e3", Side.BUY, notional=D("10")))
        assert again.reason == "real_no_armado:no_armado"
        out = await v.submit(_intent("x1", Side.SELL, qty=D("0.0001"), reduce=True))        # ...pero sale siempre
        assert out.status is OrderStatus.FILLED and fake.orders[out.order_id]["side"] == CLOSE_LONG
        assert pf.qty("BTC_USDT") == 0 and pf.realized_total < 0                 # spread + comisiones reales


async def test_la_conciliacion_frena_entradas_si_el_exchange_no_coincide(tmp_path):
    lim = limits_mod.load()
    clock = ManualClock(T0)
    fake = market(clock)
    home = tmp_path / "home"
    armed(home, lim, now=time.time())
    reg = InstrumentRegistry([Instrument("BTC_USDT", "mexc-futuros", "BTC", "USDT", kind="perp", tick_size=D("0.1"),
                                         step_size=D("0.0001"), min_qty=D("0.0001"), min_notional=D("1"))])
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        v = RealVenue(c, s, reg, ContractBooks(s, clock, {"BTC_USDT": D("0.0001")}, base=url), Portfolio(D("20")),
                      clock, home=home,
                      access_key=ACCESS, limits=lim, base=url)
        fake.positions[("BTC_USDT", 1)] = {"positionId": 9, "symbol": "BTC_USDT", "positionType": 1, "holdVol": 2,
                                          "holdAvgPrice": 99_000.0, "leverage": 1, "openType": 1}   # abierta a mano
        assert await v.reconcile() == "diferencias:BTC_USDT"
        o = await v.submit(_intent("e1", Side.BUY, notional=D("10")))
        assert o.status is OrderStatus.REJECTED and o.reason.startswith("conciliacion:")
        doge = await v.submit(OrderIntent(client_id="d", symbol="DOGE_USDT", side=Side.BUY, notional=D("5"),
                                          ref_price=D("0.2")))
        assert doge.reason in ("activo_no_armado", "conciliacion:diferencias:BTC_USDT")


async def test_operador_real_misma_decision_entra_con_stop_de_emergencia_y_sale_por_su_stop(tmp_path):
    from test_live import HACK, Alerts, news
    from tradingagent.live import LiveTrader
    from tradingagent.research.futures import MexcFutures

    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    prices = {**PRICES, "BTC_USDT": 20_000.0}         # contrato de 2 USDT: entra en una cuenta de 20 con el sobre
    fake = FakeMexcTrade(lambda sym, m: prices[sym], clock)
    home, alerts = tmp_path / "home", Alerts()
    armed(home, lim, now=time.time())
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        client = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        lev = int(lim["per_trade"]["max_leverage"])
        mk = MexcFutures(base=url, max_leverage=lev)
        mk.label = "REAL futuros MEXC"

        def venue(registry, books, pf, clk):
            return RealVenue(client, s, registry, books, pf, clk, home=home, access_key=ACCESS, limits=lim,
                             leverage=lev, base=url)

        t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=20.0, venue_factory=venue,
                       base_dir=home / "real" / mk.name, real=True)
        await t.start()
        assert "DINERO REAL" in alerts.texts()[0]
        await t.on_news(news("h1", HACK, clock.time()))                          # hackeo: corto en BTC
        pos = t.st["positions"]["BTC_USDT"]
        sent = next(o for o in fake.orders.values() if o["side"] == 3)
        assert pos["side"] == "corto" and sent["openType"] == 1 and sent["orderType"] == 3
        assert sent["stopLossPrice"] > float(pos["stop"]) > float(pos["avg_price"])  # emergencia, más allá del stop
        assert "💵 DINERO REAL" in alerts.texts()[-1] and "Vendí en corto BTC" in alerts.texts()[-1]
        prices["BTC_USDT"] = 22_000.0                                             # salta en contra: sale por su stop
        clock.advance(2)
        await t.watch_once()
        assert t.st["positions"] == {} and any(o["side"] == 2 for o in fake.orders.values())   # cerrar corto
        assert any("Perdí" in x and "DINERO REAL" in x for x in alerts.texts())
        assert "Freno del día" in alerts.texts()[-1]                              # perdió 3 % > 2 %: frena el día
        assert await t.venue.reconcile() == ""                                    # el exchange coincide
    assert (home / "real" / "mexc-futuros" / "estado.json").exists()
    assert not (home / "paper-vivo" / "mexc-futuros").exists()                    # cuentas separadas
