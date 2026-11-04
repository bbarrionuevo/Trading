"""Operador REAL en Binance (execution/live/binance_venue.py) contra un Binance falso con órdenes: entra con IOC y
tope de precio en aislado 1x, pone el stop de emergencia de cierre total y lo cancela al salir, sale siempre, un 503
no duplica órdenes, la conciliación repone el stop o frena las entradas, se sincroniza si el stop del exchange ya
cerró, el armado es por exchange y con su prueba, el simulacro va contra la DEMO y cerrar-todo cierra con tope."""
import calendar
import copy
import json
import time
from decimal import Decimal as D

import aiohttp

from conftest import serve
from fake_anthropic import KEY as AI_KEY
from fake_anthropic import FakeClaude
from fake_binance import KEY, PRICES, SECRET, FakeBinance
from tradingagent import config, paths
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.execution.instruments import Instrument, InstrumentRegistry
from tradingagent.execution.live import arming
from tradingagent.execution.live.binance_futures import DEMO, BinanceClient
from tradingagent.execution.live.binance_venue import BinanceVenue, close_everything
from tradingagent.execution.orders import OrderIntent, OrderStatus, OrderType, Side
from tradingagent.execution.portfolio import Portfolio
from tradingagent.intelligence.llm import LlmClassifier
from tradingagent.live import LiveTrader
from tradingagent.marketdata import binance_futures as bfd
from tradingagent.research.futures import BinanceFutures

T0 = calendar.timegm((2026, 11, 4, 12, 0, 0))
HACK = "Binance hacked: $40M in BTC stolen"


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)

    def texts(self):
        return [a.text for a in self.items]


def news(nid, title, now, lag=10):
    it = NewsItem(id=nid, source="media", tier=2, kind="rss", title=title, published_at=now - lag, received_at=now - 1)
    return IngestedNews(item=it, age_s=lag, freshness="fresh", dedup=DedupResult("new", nid), noise=NoiseResult(0, False),
                        accepted=True)


def armed(home, *, verified=True, days=7, now=None, key=KEY):
    now = now if now is not None else time.time()
    arming.arm(home, days, key, arming.phrase(days), venue="binance-futuros", now=now)
    if verified:
        paths.write_json_atomic(home / "state" / "verificacion_binance.json",
                                {"ok": True, "clave": arming.fingerprint(key), "ts": round(now), "simbolo": "XRPUSDT",
                                 "pasos": []})


def registry():
    return InstrumentRegistry([
        Instrument("SOLUSDT", "binance-futuros", "SOL", "USDT", kind="perp", tick_size=D("0.01"), step_size=D("0.01"),
                   min_qty=D("0.01"), min_notional=D("5")),
        Instrument("BTCUSDT", "binance-futuros", "BTC", "USDT", kind="perp", tick_size=D("0.1"),
                   step_size=D("0.001"), min_qty=D("0.001"), min_notional=D("50"))])


def intent(cid, side, *, notional=None, qty=None, reduce=False, emergency_stop=None, sym="SOLUSDT"):
    tags = {"role": "exit" if reduce else "entry", **({"emergency_stop": emergency_stop} if emergency_stop else {})}
    return OrderIntent(client_id=cid, symbol=sym, side=side, type=OrderType.MARKET, qty=qty, notional=notional,
                       max_slippage_bps=D("50"), reduce_only=reduce, ref_price=D("150"), tags=tags)


def venue(s, url, clock, home, pf, lim=None):
    return BinanceVenue(BinanceClient(s, KEY, SECRET, base=url), s, registry(), bfd.books(s, clock, url), pf, clock,
                        home=home, api_key=KEY, limits=lim or limits_mod.load(), state_path=home / "real" / "stops.json")


async def test_entra_ioc_con_stop_de_emergencia_y_sale_siempre(tmp_path):
    clock = ManualClock(T0)
    fake = FakeBinance(clock=clock)
    fake.balance = D("100")
    home, pf = tmp_path / "home", Portfolio(D("100"))
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        v = venue(s, url, clock, home, pf)
        o = await v.submit(intent("e0", Side.BUY, notional=D("16")))
        assert o.status is OrderStatus.REJECTED and o.reason == "real_no_armado:no_armado"   # sin armado, nada
        assert fake.orders == {}
        armed(home)
        big = await v.submit(intent("e1", Side.BUY, notional=D("500")))                       # hay 100 USDT
        assert big.status is OrderStatus.REJECTED and big.reason == "supera_el_saldo_disponible"
        o = await v.submit(intent("e2", Side.BUY, notional=D("16"), emergency_stop="127.50"))
        assert o.status is OrderStatus.FILLED and o.filled_qty == D("0.1") and o.fees > 0
        sent = fake.orders["e2"]
        assert sent["timeInForce"] == "IOC" and D(sent["price"]) > D("150.02")              # con tope de precio
        assert fake.margin["SOLUSDT"] == "ISOLATED" and fake.leverage["SOLUSDT"] == 1         # aislado y 1x
        stop = next(iter(fake.algos.values()))
        assert stop["closePosition"] and stop["side"] == "SELL" and stop["triggerPrice"] == "127.5"
        assert stop["workingType"] == "MARK_PRICE" and stop["algoStatus"] == "NEW"
        saved = json.loads((home / "real" / "stops.json").read_text(encoding="utf-8"))
        assert saved["stops"]["SOLUSDT"]["trigger"] == "127.50"                               # sobrevive un reinicio
        assert pf.qty("SOLUSDT") == D("0.1") and await v.reconcile() == ""
        arming.disarm(home)                                                     # revocado: no entra más...
        again = await v.submit(intent("e3", Side.BUY, notional=D("16")))
        assert again.reason == "real_no_armado:no_armado"
        out = await v.submit(intent("x1", Side.SELL, qty=D("0.1"), reduce=True))             # ...pero sale siempre
        assert out.status is OrderStatus.FILLED and fake.orders["x1"]["reduceOnly"]
        assert pf.qty("SOLUSDT") == 0 and fake.positions == {} and pf.realized_total < 0     # spread + comisiones
        assert stop["algoStatus"] == "EXPIRED" and v.stops == {}       # sin posición, Binance lo vence solo
        nada = await v.submit(intent("x2", Side.SELL, qty=D("0.1"), reduce=True))
        assert nada.reason == "reduce_only_sin_posicion"


async def test_un_503_no_es_falla_se_busca_la_orden_por_su_id(tmp_path):
    """Binance: un 503 "Unknown error" no dice si la orden entró. Se consulta por el id propio: una sola orden (D-1)."""
    clock = ManualClock(T0)
    fake = FakeBinance(clock=clock)
    fake.balance, fake.fail_next_order = D("100"), 1
    home, pf = tmp_path / "home", Portfolio(D("100"))
    armed(home)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        v = venue(s, url, clock, home, pf)
        o = await v.submit(intent("e5", Side.BUY, notional=D("16"), emergency_stop="127.50"))
    assert o.status is OrderStatus.FILLED and pf.qty("SOLUSDT") == D("0.1")
    assert sum(1 for m, p in fake.calls if m == "POST" and p == "/fapi/v1/order") == 1         # no la repitió
    assert len(fake.algos) == 1


async def test_la_conciliacion_repone_el_stop_y_frena_si_algo_no_coincide(tmp_path):
    clock = ManualClock(T0)
    fake = FakeBinance(clock=clock)
    fake.balance = D("1000")
    home, pf = tmp_path / "home", Portfolio(D("1000"))
    armed(home)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        v = venue(s, url, clock, home, pf)
        await v.submit(intent("e1", Side.BUY, notional=D("16"), emergency_stop="127.50"))
        fake.algos.clear()                                                    # alguien canceló el stop a mano
        assert await v.reconcile() == ""                                      # lo repone solo
        live = [a for a in fake.algos.values() if a["algoStatus"] == "NEW"]
        assert len(live) == 1 and live[0]["triggerPrice"] == "127.5" and live[0]["side"] == "SELL"
        fake.algos.clear()
        fake.fail_stops = 5                                                   # y si Binance no lo acepta...
        assert await v.reconcile() == "sin_stop:SOLUSDT"
        o = await v.submit(intent("e2", Side.BUY, notional=D("16")))
        assert o.reason == "conciliacion:sin_stop:SOLUSDT"                    # ...no se abre nada nuevo
        out = await v.submit(intent("x0", Side.SELL, qty=D("0.05"), reduce=True))
        assert out.status is OrderStatus.FILLED                               # pero salir, siempre
        fake.fail_stops = 0
        assert await v.reconcile() == ""
        fake.positions["BTCUSDT"] = {"amt": D("0.002"), "entry": D("100000")}   # abierta a mano en Binance
        assert await v.reconcile() == "diferencias:BTCUSDT;sin_stop:BTCUSDT"
        o = await v.submit(intent("e3", Side.BUY, notional=D("16")))
        assert o.reason.startswith("conciliacion:diferencias:BTCUSDT")


async def test_operador_real_en_binance_entra_con_stop_de_emergencia_y_sale_por_su_stop(tmp_path):
    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    prices = dict(PRICES)
    fake = FakeBinance(price=lambda sym, m: prices.get(sym), clock=clock)
    fake.balance = D("2000")
    claude = FakeClaude()
    claude.reply = {**claude.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home, alerts = tmp_path / "home", Alerts()
    armed(home)
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        mk = BinanceFutures(base=url, max_leverage=1)
        mk.label = "REAL futuros Binance"

        def factory(reg, books, pf, clk):
            return BinanceVenue(BinanceClient(s, KEY, SECRET, base=url), s, reg, books, pf, clk, home=home, api_key=KEY,
                                limits=lim, state_path=home / "real" / mk.name / "stops.json")

        llm = LlmClassifier(AI_KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=0.0, venue_factory=factory,
                           base_dir=home / "real" / mk.name, real=True, llm=llm)
            await t.start()
            assert any("DINERO REAL en futuros Binance" in x for x in alerts.texts())     # todo el saldo
            await t.on_news(news("h1", HACK, clock.time()))
            pos = t.st["positions"]["BTCUSDT"]
            assert pos["side"] == "corto" and fake.positions["BTCUSDT"]["amt"] < 0
            stop = next(a for a in fake.algos.values() if a["algoStatus"] == "NEW")
            assert stop["side"] == "BUY" and stop["closePosition"]
            assert D(stop["triggerPrice"]) == D(pos["emergency_stop"]) > D(pos["stop"]) > D(pos["avg_price"])
            assert "💵 DINERO REAL" in alerts.texts()[-1] and "Vendí en corto BTC" in alerts.texts()[-1]
            prices["BTCUSDT"] = float((D(pos["stop"]) + D(pos["emergency_stop"])) / 2)   # pasa su stop, no el del
            clock.advance(2)                                                              # exchange
            await t.watch_once()
            assert t.st["positions"] == {} and "BTCUSDT" not in fake.positions
            assert stop["algoStatus"] == "EXPIRED" and t.venue.stops == {}                # al salir, sin stop
            assert any("Perdí" in x and "DINERO REAL" in x for x in alerts.texts())
            assert await t.venue.reconcile() == ""
        finally:
            await llm.close()
    assert (home / "real" / "binance-futuros" / "estado.json").exists()


async def test_si_el_stop_del_exchange_ya_cerro_el_programa_se_sincroniza(tmp_path):
    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    prices = dict(PRICES)
    fake = FakeBinance(price=lambda sym, m: prices.get(sym), clock=clock)
    fake.balance = D("2000")
    claude = FakeClaude()
    claude.reply = {**claude.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home, alerts = tmp_path / "home", Alerts()
    armed(home)
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        mk = BinanceFutures(base=url, max_leverage=1)

        def factory(reg, books, pf, clk):
            return BinanceVenue(BinanceClient(s, KEY, SECRET, base=url), s, reg, books, pf, clk, home=home, api_key=KEY,
                                limits=lim)

        llm = LlmClassifier(AI_KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=0.0, venue_factory=factory,
                           base_dir=home / "real" / mk.name, real=True, llm=llm)
            await t.start()
            await t.on_news(news("h1", HACK, clock.time()))
            emergency = D(t.st["positions"]["BTCUSDT"]["emergency_stop"])
            prices["BTCUSDT"] = float(emergency * D("1.01"))                 # saltó más allá del stop del exchange
            n = len(fake.orders)
            clock.advance(2)
            await t.watch_once()
            assert t.st["positions"] == {} and len(fake.orders) == n         # el exchange la cerró: no mandó nada
            assert "El exchange ya la había cerrado" in " ".join(alerts.texts()[-2:])
            assert await t.venue.reconcile() == "" and t.venue.stops == {}
        finally:
            await llm.close()


def test_el_armado_es_por_exchange_y_pide_su_prueba_con_la_misma_clave(tmp_path):
    now = time.time()
    arming.arm(tmp_path, 7, KEY, arming.phrase(7), venue="binance-futuros", now=now)
    assert arming.status(tmp_path, KEY, now=now, venue="binance-futuros")[1] == "sin_verificar"
    arming.save_verification(tmp_path, {"ok": True}, KEY, now=now)                # la de MEXC no sirve
    assert arming.status(tmp_path, KEY, now=now, venue="binance-futuros")[1] == "sin_verificar"
    paths.write_json_atomic(tmp_path / "state" / "verificacion_binance.json",
                            {"ok": True, "clave": arming.fingerprint(KEY), "ts": round(now)})
    assert arming.status(tmp_path, KEY, now=now, venue="binance-futuros")[0]
    assert arming.status(tmp_path, "otra-clave", now=now, venue="binance-futuros")[1] == "clave_distinta"
    assert arming.status(tmp_path, KEY, now=now, venue="mexc-futuros")[1] == "otro_exchange"
    assert arming.asset_allowed({"activos": ["BTC"]}, "BTCUSDT") and not arming.asset_allowed({"activos": ["BTC"]},
                                                                                            "ETHUSDT")
    assert arming.venue_of({"venues": ["mexc-futuros"]}) == arming.venue_of({}) == "mexc-futuros"   # los de antes
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100, venue="binance-futuros", now=now)
    assert arming.status(tmp_path, None, now=now, venue="binance-futuros")[0]       # demo: sin prueba ni clave


def test_el_simulacro_de_binance_va_contra_la_demo(tmp_path, monkeypatch, capsys):
    from tradingagent.__main__ import main
    from tradingagent.app import Agent

    home = tmp_path / "home"
    monkeypatch.setenv("TRADING_HOME", str(home))
    assert main(["armar-real", "--simulacro"]) == 2                                 # sin las claves de la demo
    monkeypatch.setenv("BINANCE_API_KEY_TEST", "clave-demo-0000")
    monkeypatch.setenv("BINANCE_API_SECRET_TEST", "secreto-demo-0000")
    assert main(["armar-real", "--simulacro", "--dias", "3"]) == 0
    assert "DEMO de Binance" in capsys.readouterr().out
    data = json.loads((home / "ARMADO.json").read_text(encoding="utf-8"))
    assert data["venues"] == ["binance-futuros"] and data["simulacro"] and data["capital_usdt"] == 100
    env = {"BINANCE_API_KEY_TEST": "clave-demo-0000", "BINANCE_API_SECRET_TEST": "secreto-demo-0000",
           "BINANCE_API_KEY": "clave-real-no-se-usa", "BINANCE_API_SECRET": "secreto-real-no-se-usa"}
    agent = Agent(config.load(None), env)
    agent.alerts = agent.journal = None
    t = agent._build_real_trader(None, limits_mod.load())
    assert t.simulated and t.real and t.market.label == "SIMULACRO futuros Binance (demo)"
    assert t.base == home / "simulacro" / "binance-futuros"
    v = t.venue_factory(InstrumentRegistry([]), None, Portfolio(D("100")), ManualClock(T0))
    assert v.client.base == DEMO and v.books.url.startswith(DEMO) and v.simulated     # la demo, con su libro
    assert v._key == "clave-demo-0000"                                                 # nunca la clave real
    no_keys = Agent(config.load(None), {})
    no_keys.alerts = no_keys.journal = None
    assert no_keys._build_real_trader(None, limits_mod.load()) is None


async def test_cerrar_todo_cancela_stops_y_cierra_todo_con_tope(tmp_path):
    clock = ManualClock(T0)
    fake = FakeBinance(clock=clock)
    fake.positions = {"SOLUSDT": {"amt": D("0.5"), "entry": D("150")}, "BTCUSDT": {"amt": D("-0.002"),
                                                                                    "entry": D("100000")}}
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = BinanceClient(s, KEY, SECRET, base=url)
        await c.emergency_stop(symbol="SOLUSDT", side="SELL", trigger_price="120", client_id="s1")
        await c.emergency_stop(symbol="BTCUSDT", side="BUY", trigger_price="120000", client_id="s2")
        assert await close_everything(c) == []
    assert fake.positions == {} and all(a["algoStatus"] == "CANCELED" for a in fake.algos.values())
    assert all(o["reduceOnly"] and o["timeInForce"] == "IOC" for o in fake.orders.values())      # nunca a mercado


async def test_sin_el_acuerdo_tradfi_binance_rechaza_y_se_avisa_una_vez_por_dia(tmp_path):
    """Demo de Binance, 4-nov: "Please sign TradFi-Perps agreement" (-4411) al comprar TSLAUSDT. Aceptarlo es cosa
    del dueño: el operador no reintenta a ciegas y le avisa una vez por día."""
    lim = limits_mod.load()
    clock = ManualClock(T0 + 600)
    fake = FakeBinance(clock=clock)
    fake.balance, fake.tradfi_signed = D("2000"), False
    claude = FakeClaude()
    claude.reply = {**claude.reply, "category": "resultados", "assets": ["TSLA"], "polarity": 0.8, "certainty": 0.9,
                    "horizon_min": 60, "relevance": 10, "importance": 7, "impact": 7}
    home, alerts = tmp_path / "home", Alerts()
    armed(home)
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        mk = BinanceFutures(base=url, max_leverage=1)

        def factory(reg, books, pf, clk):
            return BinanceVenue(BinanceClient(s, KEY, SECRET, base=url), s, reg, books, pf, clk, home=home, api_key=KEY,
                                limits=lim)

        llm = LlmClassifier(AI_KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=alerts, home=home, capital=0.0, venue_factory=factory,
                           base_dir=home / "real" / mk.name, real=True, llm=llm)
            await t.start()
            await t.on_news(news("t1", "Tesla beats Q3 estimates as deliveries jump", clock.time()))
            assert t.st["positions"] == {} and "TSLA" in alerts.texts()[-1] and "acuerdo" in alerts.texts()[-1]
            n = len(alerts.texts())
            clock.advance(60)
            await t.on_news(news("t2", "Tesla raises 2027 delivery guidance after record quarter", clock.time()))
            assert len(alerts.texts()) == n                                   # el mismo día no se repite
        finally:
            await llm.close()


async def test_avisos_de_la_conciliacion_dicen_lo_que_pasa(tmp_path):
    """Un corte de red avisaba "las posiciones no coinciden" (el texto de vuelta no era el motivo). Ahora cada caso
    tiene su aviso, sin repetirlo cada 30 s."""
    class Venue:
        def __init__(self, seq):
            self.blocked, self.seq = "", list(seq)

        async def reconcile(self):
            self.blocked = self.seq.pop(0)
            return "sin respuesta: se cortó" if self.blocked == "exchange_sin_respuesta" else self.blocked

    alerts = Alerts()
    t = LiveTrader(BinanceFutures(), None, ManualClock(T0), copy.deepcopy(limits_mod.load()), alerts=alerts,
                   home=tmp_path, real=True)
    t.venue = Venue(["exchange_sin_respuesta", "exchange_sin_respuesta", "", "sin_stop:SOLUSDT",
                     "diferencias:BTCUSDT"])
    for _ in range(5):
        await t.reconcile()
    texts = alerts.texts()
    assert len(texts) == 4 and "Binance no responde" in texts[0] and "volvieron a coincidir" in texts[1]
    assert "sin su stop de emergencia en el exchange (SOLUSDT)" in texts[2] and "no coinciden" in texts[3]
