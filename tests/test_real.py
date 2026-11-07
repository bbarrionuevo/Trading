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


def armed(home, lim=None, *, verified=True, days=7, now=None):
    arming.arm(home, days, ACCESS, arming.phrase(days), now=now)
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


async def test_la_api_de_futuros_va_a_la_direccion_nueva_y_un_bloqueo_se_reconoce():
    """4-nov en la PC: contract.mexc.com bloqueaba en la entrada el envío de órdenes ("Access Denied", HTTP 403 en
    HTML) y mexc-real-check falló sin crear nada. La API de futuros está en api.mexc.com desde enero de 2026."""
    from aiohttp import web

    async with aiohttp.ClientSession() as s:
        assert TradingClient(s, ACCESS, SECRET).base == "https://api.mexc.com"

    async def denied(request):
        return web.Response(status=403, text="<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>",
                            content_type="text/html")

    app = web.Application()
    app.router.add_post("/api/v1/private/order/create", denied)
    async with serve(app) as url, aiohttp.ClientSession() as s:
        c = TradingClient(s, ACCESS, SECRET, base=url)
        with pytest.raises(Exception) as err:
            await c.submit(symbol="BTC_USDT", side=OPEN_LONG, vol=1, price=90_000, leverage=1, order_type=POST_ONLY,
                           external_oid="t1")
    assert "HTTP 403" in str(err.value) and "Access Denied" in str(err.value)
    assert ACCESS not in str(err.value) and SECRET not in str(err.value)


async def test_prueba_sin_riesgo_y_detecta_un_exchange_que_entiende_otro_lado():
    clock = ManualClock(T0)
    fake = market(clock)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        good = await real_check(c, s, base=url, pause_s=0)
        fake.swap_sides = True
        bad = await real_check(c, s, base=url, pause_s=0)
    assert good["ok"] and len(good["pasos"]) == 6, good
    assert not bad["ok"], bad                                             # al revés: la "solo maker" cruzaría, el
    assert any("lado" in x["detalle"] or "post only" in x["detalle"] for x in bad["pasos"])   # exchange la rechaza
    assert all(o["state"] == 4 for o in fake.orders.values())               # nada quedó colgado
    assert all(p["holdVol"] == 0 for p in fake.positions.values())          # ni una posición


def test_armado_frase_exacta_todo_el_saldo_vencimiento_y_verificacion(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(ValueError, match="frase"):
        arming.arm(home, 7, ACCESS, "dale")
    with pytest.raises(ValueError, match="30"):
        arming.arm(home, 31, ACCESS, arming.phrase(31))                       # A-2: 30 días como máximo
    assert arming.status(home, ACCESS)[1] == "no_armado"
    data = arming.arm(home, 7, ACCESS, "ARMO DINERO REAL CON TODO EL SALDO POR 7 DIAS", now=T0)
    assert data["capital_usdt"] == arming.ALL                                 # sin tope: todo el saldo
    assert arming.status(home, ACCESS, now=T0 + 60)[1] == "sin_verificar"
    arming.save_verification(home, {"ok": True}, ACCESS, now=T0)
    assert arming.status(home, ACCESS, now=T0 + 60) == (True, "", paths.read_json(home / "ARMADO.json", {}))
    assert arming.status(home, "otra-clave", now=T0 + 60)[1] == "clave_distinta"
    assert arming.status(home, ACCESS, now=T0 + 7 * 86400)[1] == "vencido"
    (home / "KILL").write_text("")
    assert arming.status(home, ACCESS, now=T0 + 60)[1] == "kill"
    (home / "KILL").unlink()
    paths.write_json_atomic(home / "ARMADO.json", {**data, "capital_usdt": 20.0})   # un armado viejo, con tope
    assert arming.status(home, ACCESS, now=T0 + 60)[1] == "volver_a_armar"
    assert arming.disarm(home) and arming.status(home, ACCESS)[1] == "no_armado"


def test_armado_sin_vencimiento_v310(tmp_path):
    """El dueño, 7-nov: "configuralo para no tener fecha de vencimiento". Con su propia frase (un error de tipeo nunca
    arma para siempre); la prueba sin riesgo tampoco vence, pero otra clave, la pausa o desarmar frenan igual."""
    home = tmp_path / "home"
    with pytest.raises(ValueError, match="frase"):
        arming.arm(home, 0, ACCESS, arming.phrase(30))                        # la frase de 30 días no sirve
    data = arming.arm(home, 0, ACCESS, "ARMO DINERO REAL CON TODO EL SALDO SIN VENCIMIENTO", now=T0)
    assert data["vence"] == arming.NEVER and arming.days_left(data) is None
    assert arming.status(home, ACCESS, now=T0 + 60)[1] == "sin_verificar"   # la prueba sigue haciendo falta
    arming.save_verification(home, {"ok": True}, ACCESS, now=T0)
    later = T0 + 400 * 86400                                                  # más de un año después
    assert arming.status(home, ACCESS, now=later)[0]
    assert arming.status(home, "otra-clave", now=later)[1] == "clave_distinta"
    (home / "KILL").write_text("")
    assert arming.status(home, ACCESS, now=later)[1] == "kill"
    (home / "KILL").unlink()
    paths.write_json_atomic(home / "ARMADO.json", {**data, "vence": "jamás"})    # algo raro en el archivo: vencido
    assert arming.status(home, ACCESS, now=later)[1] == "vencido"
    paths.write_json_atomic(home / "ARMADO.json", {k: v for k, v in data.items() if k != "vence"})
    assert arming.status(home, ACCESS, now=T0 + 60)[1] == "vencido"          # sin la fecha: vencido, como siempre
    assert arming.disarm(home) and arming.status(home, ACCESS)[1] == "no_armado"
    sim = arming.arm(home, 0, "", "", simulacro=True, capital=100)           # el simulacro también puede no vencer
    assert sim["vence"] == arming.NEVER and arming.status(home, None, now=later)[0]


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
        big = await v.submit(_intent("e1", Side.BUY, notional=D("100")))      # la cuenta tiene 20 USDT
        assert big.status is OrderStatus.REJECTED and big.reason == "supera_el_saldo_disponible"
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
    from fake_anthropic import KEY, FakeClaude
    from test_live import HACK, Alerts, news
    from tradingagent.intelligence.llm import LlmClassifier
    from tradingagent.live import LiveTrader
    from tradingagent.research.futures import MexcFutures

    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    prices = {**PRICES, "BTC_USDT": 20_000.0}         # contrato de 2 USDT: entra en una cuenta de 20 con el sobre
    fake = FakeMexcTrade(lambda sym, m: prices[sym], clock)
    claude = FakeClaude()
    claude.reply = {**claude.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home, alerts = tmp_path / "home", Alerts()
    armed(home, lim, now=time.time())
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        client = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        lev = int(lim["per_trade"]["max_leverage"])
        mk = MexcFutures(base=url, max_leverage=lev)
        mk.label = "REAL futuros MEXC"

        def venue(registry, books, pf, clk):
            return RealVenue(client, s, registry, books, pf, clk, home=home, access_key=ACCESS, limits=lim,
                             leverage=lev, base=url)

        sin_ia = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=20.0, venue_factory=venue,
                            base_dir=home / "real" / mk.name, real=True)
        await sin_ia.start()
        assert "Falta la clave de la IA" in alerts.texts()[0]
        await sin_ia.on_news(news("h0", HACK, clock.time()))                     # sin IA, con plata: nada
        assert sin_ia.st["positions"] == {} and fake.orders == {}
        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=20.0, venue_factory=venue,
                           base_dir=home / "real" / mk.name, real=True, llm=llm)
            await t.start()
            await t.on_news(news("h1", HACK, clock.time()))                      # la IA confirma: corto en BTC
            assert len(claude.requests) == 1
            pos = t.st["positions"]["BTC_USDT"]
            sent = next(o for o in fake.orders.values() if o["side"] == 3)
            assert pos["side"] == "corto" and sent["openType"] == 1 and sent["orderType"] == 3
            assert sent["stopLossPrice"] > float(pos["stop"]) > float(pos["avg_price"])  # emergencia, más allá
            assert "💵 DINERO REAL" in alerts.texts()[-1] and "Vendí en corto BTC" in alerts.texts()[-1]
            prices["BTC_USDT"] = 22_000.0                                         # salta en contra: sale por su stop
            clock.advance(2)
            await t.watch_once()
            assert t.st["positions"] == {} and any(o["side"] == 2 for o in fake.orders.values())   # cerrar corto
            assert any("Perdí" in x and "DINERO REAL" in x for x in alerts.texts())
            assert "Freno del día" in alerts.texts()[-1]                          # perdió 3 % > 2 %: frena el día
            assert await t.venue.reconcile() == ""                                # el exchange coincide
        finally:
            await llm.close()
    assert (home / "real" / "mexc-futuros" / "estado.json").exists()
    assert not (home / "paper-vivo" / "mexc-futuros").exists()                    # cuentas separadas


async def test_si_mexc_ya_la_cerro_el_programa_se_sincroniza_y_avisa(tmp_path):
    """El stop de emergencia del exchange (o el dueño a mano) cerró la posición: el cierre del programa no manda
    nada, la saca de su cartera con precio estimado, avisa, y la conciliación vuelve a coincidir."""
    from fake_anthropic import KEY, FakeClaude
    from test_live import HACK, Alerts, news
    from tradingagent.intelligence.llm import LlmClassifier
    from tradingagent.live import LiveTrader
    from tradingagent.research.futures import MexcFutures

    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    prices = {**PRICES, "BTC_USDT": 20_000.0}
    fake = FakeMexcTrade(lambda sym, m: prices[sym], clock)
    claude = FakeClaude()
    claude.reply = {**claude.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home, alerts = tmp_path / "home", Alerts()
    armed(home, lim, now=time.time())
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        client = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        mk = MexcFutures(base=url, max_leverage=1)
        mk.label = "REAL futuros MEXC"

        def venue(registry, books, pf, clk):
            return RealVenue(client, s, registry, books, pf, clk, home=home, access_key=ACCESS, limits=lim, base=url)

        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=20.0, venue_factory=venue,
                           base_dir=home / "real" / mk.name, real=True, llm=llm)
            await t.start()
            await t.on_news(news("h1", HACK, clock.time()))
            emergency = float(t.st["positions"]["BTC_USDT"]["emergency_stop"])
            fake.positions.clear()                                       # MEXC la cerró solo (stop de emergencia)
            n = len(fake.orders)
            prices["BTC_USDT"] = emergency * 1.01                        # el precio pasó el stop de emergencia
            clock.advance(2)
            await t.watch_once()
            assert t.st["positions"] == {} and len(fake.orders) == n     # no mandó ninguna orden de cierre
            assert "El exchange ya la había cerrado" in alerts.texts()[-2] + alerts.texts()[-1]
            assert await t.venue.reconcile() == ""                       # vuelve a coincidir: puede seguir operando
        finally:
            await llm.close()
    rows = [r for f in sorted((home / "real" / "mexc-futuros" / "operaciones").glob("*.jsonl"))
            for r in map(__import__("json").loads, f.read_text(encoding="utf-8").splitlines())]
    out = next(r for r in rows if r["tipo"] == "salida")
    assert out["motivo"] == "cerrada_en_el_exchange" and D(out["price"]) == D(str(emergency))


async def test_simulacro_opera_como_el_real_contra_el_libro_real_sin_mandar_ordenes(tmp_path):
    """Armado como simulacro: mismo operador real (venue real, misma decisión), pero el exchange es simulado
    contra el libro público. Ni una llamada privada a MEXC; sobrevive a un reinicio; el stop de emergencia
    también se simula."""
    from fake_anthropic import KEY, FakeClaude
    from test_live import HACK, Alerts, news
    from tradingagent.execution.live.dryrun import DryRunClient
    from tradingagent.intelligence.llm import LlmClassifier
    from tradingagent.live import LiveTrader
    from tradingagent.research.futures import MexcFutures

    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    prices = {**PRICES, "BTC_USDT": 20_000.0}
    fake = FakeMexcTrade(lambda sym, m: prices[sym], clock)
    claude = FakeClaude()
    claude.reply = {**claude.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home, alerts = tmp_path / "home", Alerts()
    data = arming.arm(home, 2, "", "", simulacro=True, capital=100, now=time.time())
    assert data["simulacro"] and data["capital_usdt"] == 100
    assert arming.status(home, None)[0]                                   # sin claves ni prueba con órdenes
    base = home / "simulacro" / "mexc-futuros"
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        mk = MexcFutures(base=url, max_leverage=1)
        mk.label = "SIMULACRO futuros MEXC"
        clients = []

        def venue(registry, books, pf, clk):
            clients.append(DryRunClient(books, s, capital=100, clock=clk, base=url,
                                        state_path=base / "exchange-simulado.json"))
            return RealVenue(clients[-1], s, registry, books, pf, clk, home=home, access_key=None, limits=lim,
                             base=url)

        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=100.0, venue_factory=venue,
                           base_dir=base, real=True, simulated=True, llm=llm)
            await t.start()
            assert "SIMULACRO" in alerts.texts()[0] and "no manda ninguna orden" in alerts.texts()[0]
            await t.on_news(news("h1", HACK, clock.time()))
            pos = t.st["positions"]["BTC_USDT"]
            body = clients[-1].sent[-1]
            assert pos["side"] == "corto" and body["side"] == 3 and body["type"] == 3 and body["openType"] == 1
            assert body["stopLossPrice"] == float(pos["emergency_stop"])  # el mismo cuerpo que recibiría MEXC
            assert "SIMULACRO" in alerts.texts()[-1] and "sin plata" in alerts.texts()[-1]
            assert fake.calls == [] and fake.orders == {}                 # ni una llamada privada a MEXC
            t2 = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=100.0, venue_factory=venue,
                            base_dir=base, real=True, simulated=True, llm=llm)
            await t2.start()                                              # reinicio de la PC: sigue todo igual
            assert await t2.venue.reconcile() == "" and t2.st["positions"]["BTC_USDT"]["side"] == "corto"
            stop, emergency = float(pos["stop"]), float(pos["emergency_stop"])
            prices["BTC_USDT"] = (stop + emergency) / 2                   # en contra, antes de la emergencia: sale
            assert stop < prices["BTC_USDT"] < emergency                  # por su propio stop (orden simulada)
            clock.advance(2)
            await t2.watch_once()
            assert t2.st["positions"] == {} and clients[-1].sent[-1]["side"] == 2
            assert await t2.venue.reconcile() == "" and clients[-1].balance < D("100")
            assert fake.calls == [] and fake.orders == {}
        finally:
            await llm.close()
    assert not (home / "real").exists()                                   # cuenta propia, aparte del real


async def test_simulacro_stop_de_emergencia_y_cerrar_lo_que_no_hay(tmp_path):
    from tradingagent.execution.live.dryrun import DryRunClient
    from tradingagent.execution.live.mexc_futures import CLOSE_SHORT, IOC, OPEN_SHORT

    clock = ManualClock(T0)
    prices = dict(PRICES)
    fake = FakeMexcTrade(lambda sym, m: prices[sym], clock)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        books = ContractBooks(s, clock, {"BTC_USDT": D("0.0001")}, base=url)
        c = DryRunClient(books, s, capital=100, clock=clock, base=url, state_path=tmp_path / "x.json")
        oid = await c.submit(symbol="BTC_USDT", side=OPEN_SHORT, vol=2, price=99_000, leverage=1, order_type=IOC,
                             external_oid="a", stop_loss=103_000)
        assert (await c.order(oid))["state"] == 3 and (await c.positions("BTC_USDT"))[0]["holdVol"] == 2
        assert (await c.deals(oid))[0]["fee"] > 0
        prices["BTC_USDT"] = 104_000.0                                    # pasa el stop de emergencia
        clock.advance(2)
        assert await c.positions() == [] and c.balance < D("100")         # el "exchange" la cerró solo
        again = DryRunClient(books, s, capital=999, clock=clock, base=url, state_path=tmp_path / "x.json")
        assert again.balance == c.balance and again.orders.keys() == c.orders.keys()   # leído del disco
        none = await c.submit(symbol="BTC_USDT", side=CLOSE_SHORT, vol=1, price=110_000, leverage=1, order_type=IOC,
                              external_oid="b")
        assert (await c.order(none))["state"] == 4                        # cerrar lo que no hay: no ejecuta nada
        with pytest.raises(ValueError):
            await c.submit(symbol="BTC_USDT", side=OPEN_SHORT, vol=1, price=1, leverage=1, order_type=MARKET,
                           external_oid="c")                              # a mercado sin tope: nunca (D-2)
    assert fake.calls == []


async def test_prueba_sin_saldo_verifica_la_clave_y_en_modo_unidireccional_avisa():
    clock = ManualClock(T0)
    fake = market(clock)
    fake.balance = D("0")
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        dry = await real_check(c, s, base=url, pause_s=0)                    # hoy, sin plata en MEXC
        fake.balance, fake.mode = D("20"), 2
        one_way = await real_check(c, s, base=url, pause_s=0)
    assert dry["solo_lectura"] and not dry["ok"] and fake.orders == {}         # ni una orden
    assert [x["ok"] for x in dry["pasos"]] == [True, True, True, False] and "falta saldo" in dry["pasos"][-1]["detalle"]
    assert not one_way["ok"] and "Cobertura" in one_way["pasos"][-1]["detalle"] and fake.orders == {}


def test_armar_el_simulacro_desde_la_consola_y_el_agente_lo_levanta_sin_claves(tmp_path, monkeypatch, capsys):
    import json

    from tradingagent import config
    from tradingagent.__main__ import main
    from tradingagent.app import Agent

    home = tmp_path / "home"
    monkeypatch.setenv("TRADING_HOME", str(home))
    for k in ("MEXC_ACCESS_KEY", "MEXC_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert main(["armar-real", "--simulacro", "--capital", "100", "--dias", "2", "--exchange", "mexc"]) == 0
    assert "Simulacro armado: 100 USDT" in capsys.readouterr().out
    data = json.loads((home / "ARMADO.json").read_text(encoding="utf-8"))
    assert data["simulacro"] and data["capital_usdt"] == 100 and (home / "state" / "parar").exists()
    agent = Agent(config.load(None), {})                      # sin claves de MEXC: el simulacro igual arranca
    agent.alerts = agent.journal = None
    t = agent._build_real_trader(None, limits_mod.load())
    assert t.simulated and t.real and t.market.label == "SIMULACRO futuros MEXC"
    assert t.base == home / "simulacro" / "mexc-futuros"
    assert main(["armar-real", "--simulacro", "--capital", "0", "--exchange", "mexc"]) == 1   # sin saldo de mentira
    assert main(["armar-real", "--simulacro", "--capital", "5000", "--exchange", "mexc"]) == 0   # sin tope
    assert main(["armar-real", "--capital", "100"]) == 1                     # el real no lleva tope: todo el saldo
    lim = limits_mod.load()
    arming.arm(home, 7, ACCESS, arming.phrase(7))
    assert main(["armar-real", "--simulacro", "--capital", "100"]) == 1      # no pisa un armado real
    assert not json.loads((home / "ARMADO.json").read_text(encoding="utf-8"))["simulacro"]
    assert agent._build_real_trader(None, lim) is None                          # real sin claves: no hay operador


async def test_sin_tope_opera_con_todo_el_saldo_y_un_retiro_no_es_una_perdida(tmp_path):
    """El dueño: "siempre se va a trabajar e invertir con todo el dinero disponible". La caja del operador real es
    el saldo de la cuenta; un depósito agranda el capital y un retiro no dispara los frenos."""
    from test_live import Alerts
    from tradingagent.live import LiveTrader
    from tradingagent.research.futures import MexcFutures

    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    fake = FakeMexcTrade(lambda sym, m: PRICES[sym], clock)
    fake.balance = D("100")                                               # el dueño puso 100 USDT
    home, alerts = tmp_path / "home", Alerts()
    armed(home, now=time.time())
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        client = TradingClient(s, ACCESS, SECRET, base=url, clock=clock)
        mk = MexcFutures(base=url, max_leverage=1)
        mk.label = "REAL futuros MEXC"

        def venue(registry, books, pf, clk):
            return RealVenue(client, s, registry, books, pf, clk, home=home, access_key=ACCESS, limits=lim, base=url)

        t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=0.0, venue_factory=venue,
                       base_dir=home / "real" / mk.name, real=True)
        await t.start()
        assert t.pf.cash == D("100") and D(t.st["initial"]) == D("100")
        assert "todo el saldo de la cuenta: 100,00 USDT" in alerts.texts()[-1]
        fake.balance = D("250")                                           # ganó o depositó: opera con todo
        clock.advance(60)
        await t.housekeeping()
        assert t.pf.cash == D("250") and "ahora opero con 250,00 USDT" in alerts.texts()[-1]
        assert D(t.st["initial"]) == D("250")                             # un depósito no es ganancia
        peak = t.st["risk"]["peak"]
        fake.balance = D("150")                                           # retiró 100: no es una pérdida
        clock.advance(60)
        await t.housekeeping()
        assert t.pf.cash == D("150") and t.st["risk"]["peak"] == peak - 100 and D(t.st["initial"]) == D("150")
        assert not t.st["risk"].get("halted") and "Frené" not in " ".join(alerts.texts())
        assert [m["usdt"] for m in t.st["movimientos_capital"]][-2:] == ["150.00000000", "-100.00000000"]
        t.st["positions"]["BTC_USDT"] = {"side": "largo"}                 # con algo abierto no se toca la caja
        fake.balance = D("10")
        await t.sync_capital()
        assert t.pf.cash == D("150")


async def test_si_el_exchange_no_responde_la_conciliacion_frena_entradas_sin_caerse(tmp_path):
    class Caido:
        async def positions(self, symbol=None):
            raise RuntimeError("se cortó internet")

    v = RealVenue(Caido(), None, InstrumentRegistry([]), None, Portfolio(D("100")), ManualClock(T0),
                  home=tmp_path, access_key=ACCESS, limits=limits_mod.load())
    assert (await v.reconcile()).startswith("sin respuesta") and v.blocked == "exchange_sin_respuesta"
