"""Binance Futures (modo real) contra un Binance falso: firma, lista cerrada de operaciones (A-3), nada de órdenes a
mercado sin tope (D-2), pausa ante límites (429) para no ser baneados, y la prueba sin riesgo."""
import aiohttp
import pytest

from conftest import serve
from fake_binance import KEY, SECRET, FakeBinance
from tradingagent.execution.live.binance_check import real_check
from tradingagent.execution.live.binance_futures import (
    BUY,
    GTX,
    IOC,
    SELL,
    BinanceClient,
    BinanceError,
    allowed,
    fmt,
)


async def test_firma_lista_cerrada_y_nunca_a_mercado_sin_tope():
    fake = FakeBinance()
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = BinanceClient(s, KEY, SECRET, base=url)
        await c.sync_time()
        assert (await c.usdt())["available"] == 50.0 and await c.positions() == []
        o = await c.submit(symbol="XRPUSDT", side=BUY, qty="10", price="0.4850", tif=GTX, client_id="a1")
        assert o["status"] == "NEW" and (await c.order("XRPUSDT", "a1"))["timeInForce"] == GTX
        await c.cancel("XRPUSDT", "a1")
        for method, path in (("POST", "/sapi/v1/asset/transfer"), ("POST", "/sapi/v1/futures/transfer"),
                             ("POST", "/sapi/v1/capital/withdraw/apply"), ("POST", "/fapi/v1/listenKey"),
                             ("POST", "/fapi/v1/batchOrders"), ("POST", "/fapi/v1/positionSide/dual"),
                             ("POST", "/fapi/v1/multiAssetsMargin"), ("GET", "/fapi/v1/order/test")):
            with pytest.raises(PermissionError):
                await c.request(method, path)
        with pytest.raises(PermissionError):
            await c.public("/sapi/v1/account/apiRestrictions")
        for bad in ({"tif": None}, {"tif": "GTC"}, {"price": None}):                 # ni a mercado ni GTC colgada
            with pytest.raises(ValueError):
                await c.submit(**{"symbol": "XRPUSDT", "side": BUY, "qty": "10", "price": "0.5", "tif": IOC,
                                  "client_id": "x", **bad})
        with pytest.raises(TypeError):                                            # ni a mercado ni stops por acá
            await c.submit(symbol="XRPUSDT", side=BUY, qty="10", client_id="m", order_type="MARKET")
        st = await c.emergency_stop(symbol="XRPUSDT", side=SELL, trigger_price="0.45", client_id="e1")
        assert st["closePosition"] and st["orderType"] == "STOP_MARKET" and st["workingType"] == "MARK_PRICE"
        assert len(await c.open_stops("XRPUSDT")) == 1
        await c.cancel_stop("e1")
        assert (await c.stop("e1"))["algoStatus"] == "CANCELED" and await c.open_stops("XRPUSDT") == []
    assert fake.signed and all(fake.signed)
    assert not any("transfer" in p or "withdraw" in p for _, p in fake.calls)       # ni siquiera salió a la red
    assert allowed("POST", "/fapi/v1/order") and not allowed("PUT", "/fapi/v1/order")
    assert fmt("0.48500") == "0.485" and fmt(1e-5) == "0.00001"


async def test_ante_un_429_hace_pausa_y_no_insiste():
    fake = FakeBinance()
    fake.throttle = 1
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        c = BinanceClient(s, KEY, SECRET, base=url)
        with pytest.raises(BinanceError) as err:
            await c.usdt()
        assert err.value.code == 429 and c.paused_until > 0
        n = len(fake.calls)
        with pytest.raises(BinanceError):
            await c.usdt()                                                        # en pausa: ni sale a la red
        assert len(fake.calls) == n


async def test_prueba_sin_riesgo_espera_antes_de_cancelar_y_valida_el_stop():
    fake = FakeBinance()
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        rep = await real_check(BinanceClient(s, KEY, SECRET, base=url), pause_s=0.2)
    assert rep["ok"], rep["pasos"]
    assert fake.margin["XRPUSDT"] == "ISOLATED" and fake.leverage["XRPUSDT"] == 1
    assert [(t["type"], t.get("timeInForce")) for t in fake.tests] == [("LIMIT", IOC)]    # la entrada, sin ejecutar
    assert [o["status"] for o in fake.orders.values()] == ["CANCELED"]
    stop = next(iter(fake.algos.values()))                                         # el stop, por el servicio Algo
    assert stop["orderType"] == "STOP_MARKET" and stop["algoStatus"] == "CANCELED" and stop["side"] == BUY
    assert float(stop["triggerPrice"]) > float(fake.ask) * 1.49                    # solo con +50 %: no se dispara
    assert len(fake.cancel_after) == 2 and min(fake.cancel_after) >= 0.2           # esperó antes de cancelar
    assert all(p["detalle"] == "" for p in rep["pasos"])                         # sin ayudas si todo da bien


@pytest.mark.parametrize("setup,paso", [
    (lambda f: setattr(f, "multi_assets", True), "modo de un solo activo"),
    (lambda f: setattr(f, "dual", True), "modo de posición unidireccional"),
    (lambda f: setattr(f, "locked", True), "reglas cuantitativas sin bloqueos"),
    (lambda f: setattr(f, "swap_sides", True), "orden de prueba solo maker"),
])
async def test_prueba_sin_riesgo_detecta_lo_que_falta(setup, paso):
    fake = FakeBinance()
    setup(fake)
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        rep = await real_check(BinanceClient(s, KEY, SECRET, base=url), pause_s=0)
    assert not rep["ok"] and any(p["paso"] == paso and not p["ok"] for p in rep["pasos"]), rep["pasos"]
    assert all(o["status"] == "CANCELED" for o in fake.orders.values())              # nada quedó colgado
    assert all(a["algoStatus"] == "CANCELED" for a in fake.algos.values())


async def test_sin_saldo_solo_lee_y_no_pone_ninguna_orden():
    fake = FakeBinance()
    fake.balance = fake.balance * 0 + 3
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        rep = await real_check(BinanceClient(s, KEY, SECRET, base=url), pause_s=0)
    assert not rep["ok"] and rep.get("solo_lectura") and fake.orders == {}
    assert "falta saldo" in rep["pasos"][-1]["detalle"]
