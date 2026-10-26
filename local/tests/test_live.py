"""Ejecución real (Fase 6) contra un MEXC simulado: llenados del exchange, parciales, rechazos,
resultados desconocidos (R-05), IDs propios (R-06), diario con hash (R-10), KILL (R-16) y autorización."""
import json

import pytest

INFO = {"symbols": [{"symbol": "XUSDT", "baseAsset": "X", "quotePrecision": 4, "baseAssetPrecision": 2,
                     "quoteAmountPrecision": "1", "orderTypes": ["LIMIT", "MARKET", "LIMIT_MAKER"],
                     "isSpotTradingAllowed": True}]}
RISK = {"max_position_eur": 40, "max_total_exposure_eur": 80, "daily_loss_limit_eur": 2, "weekly_loss_limit_eur": 4,
        "max_drawdown_eur": 8, "max_consecutive_stops": 3, "stops_cooldown_min": 60, "max_orders_per_minute": 30,
        "losing_days_window": 7}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeMexc:
    """Órdenes con la semántica de MEXC spot v3 que usa el agente."""

    def __init__(self):
        from nexus.exchanges.mexc_trade import OrderRejected
        self.Rej = OrderRejected
        self.orders, self.calls, self.fail, self.reject = {}, [], {}, {}
        self.market_px, self.depth = None, 1e9                 # depth: cuánto hay comprando (libro finito)
        self.balances = {"X": 0.0, "USDT": 100.0}
        self.on_new = None

    def _maybe_fail(self, name):
        if self.fail.get(name):
            self.fail[name] -= 1
            raise ConnectionError(f"corte de red en {name}")
        if self.reject.get(name):
            self.reject[name] -= 1
            raise self.Rej(400, 30004, "Insufficient position")

    def new_order(self, symbol, side, type_, quantity, price=None, cid=None):
        self.calls.append(("new", side, type_, quantity, price, cid))
        if self.on_new:
            self.on_new(cid)
        self._maybe_fail("new")
        o = {"clientOrderId": cid, "symbol": symbol, "side": side, "type": type_, "price": price or "0",
             "origQty": quantity, "executedQty": "0", "cummulativeQuoteQty": "0", "status": "NEW"}
        self.orders[cid] = o
        if type_ == "MARKET":
            self.fill(cid, float(quantity), self.market_px)
        if type_ == "LIMIT" and side == "SELL" and self.market_px is not None and float(price) <= self.market_px:
            self.fill(cid, min(float(quantity), self.depth), self.market_px)          # límite agresivo: cruza
        return {"orderId": len(self.orders), "symbol": symbol}

    def fill(self, cid, qty, px=None):
        o = self.orders[cid]
        px = px if px is not None else float(o["price"])
        ex = float(o["executedQty"]) + qty
        o["executedQty"] = f"{ex}"
        o["cummulativeQuoteQty"] = f"{float(o['cummulativeQuoteQty']) + qty * px}"
        o["status"] = "FILLED" if ex >= float(o["origQty"]) - 1e-12 else "PARTIALLY_FILLED"
        sign = 1 if o["side"] == "BUY" else -1
        self.balances["X"] += sign * qty
        self.balances["USDT"] -= sign * qty * px

    def get_order(self, symbol, cid):
        self.calls.append(("get", cid))
        self._maybe_fail("get")
        if cid not in self.orders:
            raise self.Rej(400, -2013, "Order does not exist.")
        return dict(self.orders[cid])

    def cancel_order(self, symbol, cid):
        self.calls.append(("cancel", cid))
        self._maybe_fail("cancel")
        o = self.orders.get(cid)
        if o is None:
            raise self.Rej(400, -2011, "Unknown order sent.")
        if o["status"] in ("NEW", "PARTIALLY_FILLED"):
            o["status"] = "CANCELED" if float(o["executedQty"]) == 0 else "PARTIALLY_CANCELED"
        return dict(o)

    def open_orders(self, symbol):
        return [dict(o) for o in self.orders.values() if o["status"] in ("NEW", "PARTIALLY_FILLED")]

    def account(self):
        return {"canTrade": True, "canWithdraw": False,
                "balances": [{"asset": a, "free": str(v), "locked": "0"} for a, v in self.balances.items()]}

    def exchange_info(self, symbol):
        return INFO

    def _get(self, path, params=None, signed=False):
        """Libro de datos (cliente de solo lectura): el mejor nivel de cada lado, sin nuestras órdenes."""
        assert path == "/api/v3/depth"
        bt = self.book_ticker(params["symbol"])
        return {"bids": [[bt["bidPrice"], bt["bidQty"]]], "asks": [[bt["askPrice"], bt["askQty"]]]}

    def open_cids(self):
        return [c for c, o in self.orders.items() if o["status"] in ("NEW", "PARTIALLY_FILLED")]


@pytest.fixture
def env(tmp_path):
    from nexus import paths
    from nexus.agent import live
    from nexus.agent.maker_agent import Gate
    from nexus.agent.risk import RiskManager
    ex, clock, closed = FakeMexc(), Clock(), []
    gate = Gate(RiskManager(RISK, state_path=paths.state("risk_real.json"), now=clock))
    saved = {}

    def make(**kw):
        bk = live.LiveBook("XUSDT", ex, live.Rules(INFO, "XUSDT"), live.OrderLog(), save=lambda: None, now=clock,
                           notional=20, maker=0.0, taker=0.05, stop_pct=1.0, min_edge_pct=0.0, max_hold_s=1800,
                           gate=gate, on_closed=closed.append, **kw)
        bk.save_cb = lambda: saved.update(XUSDT=json.loads(json.dumps(bk.to_dict())))
        gate.books["XUSDT"] = bk
        return bk
    return {"ex": ex, "clock": clock, "closed": closed, "gate": gate, "make": make, "saved": saved, "live": live}


def test_vuelta_completa_con_llenados_del_exchange(env):
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0000, 500, 1.0010, 500)
    new = [c for c in ex.calls if c[0] == "new"]
    assert new == [("new", "BUY", "LIMIT_MAKER", "20.00", "1.0000", "nx1XUSDT")]
    ex.fill("nx1XUSDT", 20.0)
    bk.on_book(1.0000, 500, 1.0010, 500)                         # ve el llenado: comprado
    assert bk.state == "long" and bk.pos_qty == 20.0 and bk.entry == 1.0
    bk.on_book(1.0000, 500, 1.0010, 500)                         # pone la venta en el ask
    sell = [c for c in ex.calls if c[0] == "new"][-1]
    assert sell[1:5] == ("SELL", "LIMIT_MAKER", "20.00", "1.0010")
    ex.fill(sell[5], 20.0)
    bk.on_book(1.0005, 500, 1.0015, 500)
    assert bk.state == "flat" and bk.rt == 1 and bk.wins == 1
    t = env["closed"][0]
    assert t["real"] and t["kind"] == "maker" and abs(t["pnl"] - 0.02) < 1e-9
    assert env["gate"].risk.s["pnl_day"] == pytest.approx(0.02)
    assert env["live"].verify_chain(bk.olog.path)


def test_id_propio_se_guarda_antes_de_enviar_y_requote_cancela(env):
    ex, bk, saved = env["ex"], env["make"](), env["saved"]
    seen = []
    ex.on_new = lambda cid: seen.append((cid, (saved["XUSDT"]["order"] or {}).get("cid")))
    bk.on_book(1.0000, 500, 1.0010, 500)
    bk.on_book(1.0002, 500, 1.0010, 500)                         # sube el bid: cancela y re-cotiza en el mismo ciclo
    assert ex.orders["nx1XUSDT"]["status"] == "CANCELED" and bk.order["cid"] == "nx2XUSDT"
    assert seen == [("nx1XUSDT", "nx1XUSDT"), ("nx2XUSDT", "nx2XUSDT")]   # R-06: guardado antes de enviar
    assert ex.open_cids() == ["nx2XUSDT"] and ex.orders["nx2XUSDT"]["price"] == "1.0002"
    bk.on_book(1.0002, 500, 1.0010, 500)                         # mismo precio: no toca la orden
    assert ex.open_cids() == ["nx2XUSDT"]


def test_compra_parcial_y_parcial_chica(env):
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0, 500, 1.001, 500)
    ex.fill("nx1XUSDT", 0.5)                                     # 0,5 USDT: menos que el mínimo de 1
    bk.on_book(0.999, 500, 1.001, 500)                           # el bid baja: cancela
    assert bk.state == "flat" and bk.pos_qty == 0.5
    bk.on_book(0.999, 500, 1.001, 500)                           # compra solo lo que falta
    assert [c for c in ex.calls if c[0] == "new"][-1][3] == "19.51"
    ex.fill("nx2XUSDT", 10.0)
    bk.on_book(0.998, 500, 1.001, 500)                           # parcial suficiente: queda comprado
    assert bk.state == "long" and bk.pos_qty == pytest.approx(10.5)


def test_corte_de_red_al_enviar_bloquea_hasta_conciliar(env):
    ex, bk, clock = env["ex"], env["make"](), env["clock"]
    ex.fail["new"] = 1                                           # el pedido no llegó (o sí: no sabemos)
    bk.on_book(1.0, 500, 1.001, 500)
    assert bk.unknown and len([c for c in ex.calls if c[0] == "new"]) == 1
    bk.on_book(1.0002, 500, 1.001, 500)                          # no manda nada nuevo mientras no sabe
    assert len([c for c in ex.calls if c[0] == "new"]) == 1
    clock.t += 5
    bk.on_book(1.0002, 500, 1.001, 500)                          # 2ª consulta: no existe => se libera
    assert bk.unknown is None and bk.order["cid"] == "nx2XUSDT"  # y recién ahí vuelve a cotizar
    assert "nx1XUSDT" not in ex.orders
    ex2, bk2 = FakeMexc(), env["make"]()
    bk2.trader = ex2
    ex2.fail["get"], ex2.fail["cancel"] = 0, 0
    bk2.on_book(1.0, 500, 1.001, 500)
    ex2.fail["cancel"] = 1                                       # la cancelación se corta: estado desconocido
    bk2.on_book(1.0005, 500, 1.001, 500)
    assert bk2.unknown and ex2.orders["nx1XUSDT"]["status"] == "NEW"
    ex2.fill("nx1XUSDT", 20.0)                                   # mientras tanto se llenó
    bk2.on_book(1.0005, 500, 1.001, 500)
    assert bk2.unknown is None and bk2.state == "long"           # la conciliación encuentra el llenado


def test_stop_cancela_la_venta_y_vende_a_mercado(env):
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0, 500, 1.001, 500)
    ex.fill("nx1XUSDT", 20.0)
    bk.on_book(1.0, 500, 1.001, 500)
    bk.on_book(1.0, 500, 1.001, 500)
    ex.market_px = 0.985
    bk.on_book(0.985, 500, 0.986, 500)                           # cae 1,5%: stop
    assert ex.orders["nx2XUSDT"]["status"] == "CANCELED"
    o = ex.orders["nx3XUSDT"]                                    # límite agresivo con tope de 0,5% bajo el bid
    assert o["type"] == "LIMIT" and o["price"] == "0.9800" and bk.state == "flat" and bk.stops == 1
    t = env["closed"][0]
    assert t["kind"] == "stop" and t["pnl"] == pytest.approx(20 * 0.985 * (1 - 0.0005) - 20.0)
    assert env["gate"].risk.s["stops_row"] == 1


def test_kill_2_cancela_y_kill_3_vende(env):
    from nexus import paths
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0, 500, 1.001, 500)
    ex.fill("nx1XUSDT", 20.0)
    bk.on_book(1.0, 500, 1.001, 500)
    bk.on_book(1.0, 500, 1.001, 500)
    assert ex.open_cids() == ["nx2XUSDT"]
    paths.kill_file().write_text("2")
    bk.on_book(1.0, 500, 1.001, 500)
    assert ex.open_cids() == [] and bk.state == "long"           # nivel 2: sin órdenes, posición intacta
    bk.on_book(1.0, 500, 1.001, 500)
    assert ex.open_cids() == []
    paths.kill_file().write_text("3")
    ex.market_px = 0.999
    bk.on_book(0.999, 500, 1.001, 500)
    assert bk.state == "flat" and env["closed"][0]["kind"] == "kill"


def test_rechazos_seguidos_bloquean_el_par(env):
    ex, bk = env["ex"], env["make"]()
    ex.reject["new"] = 99
    for _ in range(6):
        bk.on_book(1.0, 500, 1.001, 500)
    assert bk.blocked and "rechazos" in bk.blocked and len([c for c in ex.calls if c[0] == "new"]) == 5


def test_venta_rechazada_por_comision_en_moneda_vende_lo_que_hay(env):
    """26-oct, BDX: el par cobra la comisión en la moneda comprada; el llenado informa la cantidad
    bruta y la venta pedía más de lo que había (30004) hasta bloquear el par."""
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0, 500, 1.001, 500)
    ex.fill("nx1XUSDT", 20.0)
    ex.balances["X"] = 19.98                                     # 0,1% de comisión cobrada en X
    ex.reject["new"] = 1
    bk.on_book(1.0, 500, 1.001, 500)
    bk.on_book(1.0, 500, 1.001, 500)                             # la venta de 20 se rechaza: lee el saldo
    assert bk.pos_qty == 19.98 and bk.rejects == 0 and not bk.blocked
    bk.on_book(1.0, 500, 1.001, 500)
    assert [c for c in ex.calls if c[0] == "new"][-1][1:4] == ("SELL", "LIMIT_MAKER", "19.98")
    ex2, bk2 = env["ex"], env["make"]()                          # si falta mucho más que una comisión: bloqueo normal
    ex2.balances["X"] = 5.0
    bk2.state, bk2.pos_qty, bk2.qty, bk2.entry, bk2.entry_ts = "long", 20.0, 20.0, 1.0, env["clock"]()
    ex2.reject["new"] = 99
    for _ in range(6):
        bk2.on_book(1.0, 500, 1.001, 500)
    assert bk2.blocked and "rechazos" in bk2.blocked


def test_sin_autorizacion_no_entra_pero_sale(env):
    ex, bk = env["ex"], env["make"]()
    bk.entry_allowed = lambda: False
    bk.on_book(1.0, 500, 1.001, 500)
    assert not [c for c in ex.calls if c[0] == "new"]
    bk.entry_allowed = lambda: True
    bk.on_book(1.0, 500, 1.001, 500)
    ex.fill("nx1XUSDT", 20.0)
    bk.entry_allowed = lambda: False                             # vence con la posición abierta
    bk.on_book(1.0, 500, 1.001, 500)
    bk.on_book(1.0, 500, 1.001, 500)
    assert bk.state == "long" and ex.open_cids() == ["nx2XUSDT"]  # la venta se coloca igual


def test_al_arrancar_concilia_orden_guardada_huerfanas_y_saldo(env):
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0, 500, 1.001, 500)
    state = bk.to_dict()
    ex.fill("nx1XUSDT", 20.0)                                    # se llenó con el agente apagado
    ex.new_order("XUSDT", "BUY", "LIMIT_MAKER", "5.00", "0.9000", "nx9XUSDT")   # huérfana propia
    bk2 = env["make"]()
    assert bk2.load(state) and bk2.order["cid"] == "nx1XUSDT"
    bk2.reconcile(ex.account()["balances"], 1.0)
    assert bk2.state == "long" and bk2.pos_qty == 20.0 and not bk2.blocked
    assert ex.orders["nx9XUSDT"]["status"] == "CANCELED"
    bk3 = env["make"]()                                          # el saldo no coincide: bloqueado
    bk3.reconcile([{"asset": "X", "free": "35", "locked": "0"}], 1.0)
    assert bk3.blocked and "saldo" in bk3.blocked
    ex.new_order("XUSDT", "BUY", "LIMIT", "5.00", "0.9000", "manual1")          # orden ajena
    bk4 = env["make"]()
    bk4.reconcile([], 1.0)
    assert bk4.blocked and "no es de nexus" in bk4.blocked


def test_diario_de_ordenes_detecta_alteraciones(env, tmp_path):
    live = env["live"]
    lg = live.OrderLog(tmp_path / "o.jsonl")
    lg.write({"act": "enviar", "qty": 1})
    lg.write({"act": "ejecutada", "qty": 1})
    assert live.verify_chain(tmp_path / "o.jsonl")
    lines = (tmp_path / "o.jsonl").read_text().splitlines()
    (tmp_path / "o.jsonl").write_text(lines[0].replace('"qty": 1', '"qty": 9') + "\n" + lines[1] + "\n")
    assert not live.verify_chain(tmp_path / "o.jsonl")


def answers(*xs):
    it = iter(xs)
    return lambda prompt: next(it)


def test_autorizacion_topes_frase_y_revocar():
    from nexus import paths
    from nexus.agent import authorize, live
    ok_status = {"evidence": {"criteria_met": 6, "criteria_total": 6}}
    assert authorize.validate(101, 7, ["XUSDT"], False, ok_status)             # más de 100 € (el capital del dueño)
    assert authorize.validate(20, 8, ["XUSDT"], False, ok_status)              # más de 7 días
    assert authorize.validate(20, 7, ["XUSDT"], False, {"evidence": {"criteria_met": 2, "criteria_total": 6}})
    assert not authorize.validate(20, 7, ["XUSDT"], True, {"evidence": {"criteria_met": 2, "criteria_total": 6}})
    out = []
    args = ["--capital", "20", "--dias", "7", "--pares", "XUSDT", "--calibracion"]
    assert authorize.main(args, ask=answers("SI", "autorizo"), out=out.append, now=1e9) == 2
    assert live.authorization(1e9 + 1) is None                                  # frase incorrecta: nada
    assert authorize.main(args, ask=answers("no", authorize.PHRASE), out=out.append, now=1e9) == 2
    assert authorize.main(args, ask=answers("SI", authorize.PHRASE), out=out.append, now=1e9) == 0
    a = live.authorization(1e9 + 3600)
    assert a["symbols"] == ["XUSDT"] and a["capital_eur"] == 20 and a["calibracion"]
    assert live.authorization(1e9 + 8 * 86400) is None                         # vencida
    authorize.main(["--revocar"], out=out.append, now=1e9 + 7200)
    assert live.authorization(1e9 + 7201) is None
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": 1e9, "expires": 1e9 + 86400, "capital_eur": 150,
                                                           "symbols": ["XUSDT"]})
    assert live.authorization(1e9 + 1) is None                                  # editada a mano por encima del tope


def test_agente_activa_el_modo_real_solo_con_autorizacion(monkeypatch):
    import time
    from nexus import paths
    from nexus.agent import live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    cfg = ma.load_config()
    fake = FakeMexc()
    fake.has_keys = True
    fake.book_ticker = lambda s: {"bidPrice": "1.0", "bidQty": "5", "askPrice": "1.001", "askQty": "5"}
    fake.trade_fee = lambda s: {"makerCommission": 0, "takerCommission": 0.0005}
    monkeypatch.setattr(mt, "MexcTrader", lambda: fake)
    assert ma.build_desk(cfg, fake, 0.0, 0.05) is None                         # sin autorización: paper
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": time.time(), "expires": time.time() + 3600,
                                                           "capital_eur": 20, "symbols": ["XUSDT"],
                                                           "sin_retiro_confirmado": True})
    fake.balances["USDT"] = 15.0
    assert ma.build_desk(cfg, fake, 0.0, 0.05).books["XUSDT"].notional == 14.7   # compra con lo que hay
    fake.balances["USDT"] = 100.0
    desk = ma.build_desk(cfg, fake, 0.0, 0.05)
    assert list(desk.books) == ["XUSDT"] and desk.books["XUSDT"].notional == 20
    assert desk.gate.risk.path == paths.state("risk_real.json")                # riesgo real aparte del paper
    for _ in range(11):                                          # el filtro de volatilidad necesita datos
        desk.step(20)
    assert fake.open_cids() == ["nx1XUSDT"]
    desk.shutdown()                                                             # reinicio: no deja órdenes
    assert fake.open_cids() == [] and json.loads(paths.state(live.STATE_FILE).read_text())["XUSDT"]["model"] == "real"
    fake.account = lambda: {"canTrade": True, "canWithdraw": True, "balances": []}
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": time.time(), "expires": time.time() + 3600,
                                                           "capital_eur": 20, "symbols": ["XUSDT"]})
    assert ma.build_desk(cfg, fake, 0.0, 0.05) is None                         # R-01 sin confirmar


def test_cliente_de_trading_solo_endpoints_de_ordenes():
    from nexus.exchanges.base import ReadOnlyViolation
    from nexus.exchanges.mexc_trade import MexcTrader, OrderRejected
    t = MexcTrader("k", "s")
    with pytest.raises(ReadOnlyViolation):
        t._send("POST", "/api/v3/capital/withdraw/apply", {})
    with pytest.raises(ReadOnlyViolation):
        t._send("DELETE", "/api/v3/openOrders", {})

    class R:
        status_code = 400
        text = ""

        def json(self):
            return {"code": -2013, "msg": "Order does not exist."}
    t.s.request = lambda *a, **k: R()
    with pytest.raises(OrderRejected) as e:
        t.get_order("XUSDT", "nx1XUSDT")
    assert e.value.not_found and e.value.status == 400


def test_desbloqueo_humano_adopta_el_saldo_como_residuo(env):
    from nexus import paths
    from nexus.agent import authorize, live
    ex, bk = env["ex"], env["make"]()
    bk.reconcile([{"asset": "X", "free": "35", "locked": "0"}], 1.0)
    assert bk.blocked
    ex.balances["X"] = 35.0
    desk = live.Desk(ex, None, env["gate"], {"XUSDT": bk}, bk.olog)
    desk.save = lambda: None
    assert authorize.main(["--desbloquear", "XUSDT"], ask=answers("no"), out=lambda m: None) == 2
    assert authorize.main(["--desbloquear", "XUSDT"], ask=answers("SI"), out=lambda m: None) == 0
    desk.unblock_requests()
    assert bk.blocked is None and bk.dust == 35.0 and bk.state == "flat"
    assert not paths.state(live.UNBLOCK_FILE).exists()
    bk.check_balance(ex.account()["balances"], 1.0)
    assert bk.blocked is None                                    # el residuo ya es lo esperado


def test_informe_muestra_el_dinero_real():
    from nexus.monitor import digest
    lv = {"auth": {"capital_eur": 20, "symbols": ["XUSDT"], "calibracion": True}, "kill_level": 0,
          "real": {"n": 3, "total": -0.12, "days": 1.5}, "risk": {"pnl_day": -0.12, "pnl_week": -0.12},
          "books": [{"sym": "XUSDT", "state": "flat", "pos_qty": 0, "dust": 0.01, "pnl_eur": -0.12,
                     "blocked": "saldo distinto"}]}
    md = digest({"status": {"evidence": {}, "books": [], "live": lv}}, None, now=1_800_000_000)
    assert "Dinero real" in md and "hasta 20 € en XUSDT" in md and "calibración" in md and "BLOQUEADO" in md


def test_vigilante_avisa_par_real_bloqueado():
    import time
    from nexus.monitor import alerts
    pc = {"ts": time.time(), "status": {"ts": time.time(), "live": {"books": [{"sym": "XUSDT", "blocked": "saldo distinto"}],
                                                                   "risk": {"sticky_halt": "drawdown"}}}}
    a = " | ".join(alerts(pc))
    assert "XUSDT BLOQUEADO" in a and "HALT PERMANENTE" in a and "--rearmar --real" in a


def test_agente_completo_en_modo_real_hasta_el_reinicio(monkeypatch):
    """main() de punta a punta con autorización: el libro real opera, el estado lo publica, el par
    real bloquea la actualización mientras está comprado y el reinicio no deja órdenes en MEXC."""
    import itertools
    import time
    from nexus import paths
    from nexus.agent import live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    from tests.test_windows import FakeMexc as FakeData
    monkeypatch.setattr(ma.time, "sleep", lambda s: None)
    monkeypatch.setattr(ma, "load_config", lambda: {**ma.yaml.safe_load(ma.CFG.read_text(encoding="utf-8")),
                                                    "max_vol_1m_pct": None})
    ex, calls, filled = FakeMexc(), itertools.count(), []
    ex.has_keys = True

    class Data(FakeData):
        has_keys = False

        def book_ticker(self, s):
            n = next(calls)
            buys = [c for c in ex.open_cids() if ex.orders[c]["side"] == "BUY"]
            if s == "XUSDT" and n >= 40 and buys and not filled:
                filled.append(buys[0])                       # se llena la compra real
                ex.fill(buys[0], float(ex.orders[buys[0]]["origQty"]))
            if n >= 120:
                paths.state("RESTART").touch()
            return super().book_ticker(s)
    monkeypatch.setattr(ma, "MexcReadOnly", Data)
    monkeypatch.setattr(mt, "MexcTrader", lambda: ex)
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": time.time(), "expires": time.time() + 3600,
                                                           "capital_eur": 20, "symbols": ["XUSDT"],
                                                           "sin_retiro_confirmado": True})
    ma.main()
    st = json.loads(paths.state("status.json").read_text())
    assert st["mode"] == "real+paper" and st["live"]["books"][0]["sym"] == "XUSDT" and filled
    assert "XUSDT" in [b["sym"] for b in st["lab"]]                # gemelo en paper del par real
    books = json.loads(paths.state("paper_books.json").read_text())
    assert books["XUSDT#real"]["state"] == "long"                # el actualizador ve la posición real
    assert ex.open_cids() == []                                   # al reiniciar cancela la venta puesta
    assert live.verify_chain(paths.state(live.ORDERS_LOG))


def test_salida_forzada_en_libro_finito_reprecia_sin_pasar_el_tope(env):
    ex, bk, clock = env["ex"], env["make"](), env["clock"]
    bk.on_book(1.0, 500, 1.001, 500)
    ex.fill("nx1XUSDT", 20.0)
    bk.on_book(1.0, 500, 1.001, 500)
    bk.on_book(1.0, 500, 1.001, 500)
    ex.market_px, ex.depth = 0.985, 8.0                          # solo hay 8 unidades comprando
    bk.on_book(0.985, 8, 0.986, 500)
    assert bk.state == "long" and bk.pos_qty == pytest.approx(12.0) and bk.order["kind"] == "stop"
    bk.on_book(0.985, 8, 0.986, 500)                             # antes de 3 s no toca la orden
    assert bk.order["cid"] == "nx3XUSDT"
    clock.t += 4
    ex.market_px, ex.depth = 0.983, 1e9
    bk.on_book(0.983, 500, 0.984, 500)                           # re-precio al bid nuevo: se completa
    assert bk.state == "flat" and ex.orders["nx3XUSDT"]["status"] == "PARTIALLY_CANCELED"
    assert ex.orders["nx4XUSDT"]["price"] == "0.9780" and env["closed"][0]["kind"] == "stop"
    assert env["closed"][0]["qty"] == pytest.approx(20.0)


def test_reglas_rechazan_pares_no_operables():
    from nexus.agent import live
    bad = {"symbols": [INFO["symbols"][0] | {"status": "2"}]}
    with pytest.raises(ValueError):
        live.Rules(bad, "XUSDT")
    with pytest.raises(ValueError):
        live.Rules({"symbols": [INFO["symbols"][0] | {"orderTypes": ["LIMIT", "MARKET"]}]}, "XUSDT")
    assert live.Rules({"symbols": [INFO["symbols"][0] | {"status": "1"}]}, "XUSDT").min_quote == 1.0


def test_un_solo_agente_por_pc(monkeypatch):
    import nexus.agent.maker_agent as ma
    held = ma.single_instance()
    assert held is not None and ma.single_instance() is None       # un segundo proceso no puede
    monkeypatch.setattr(ma, "run", lambda: (_ for _ in ()).throw(AssertionError("no debía operar")))
    ma.main(wait_if_busy=0)                                         # sale sin operar
    held.close()
    again = ma.single_instance()
    assert again is not None                                        # liberado al cerrar
    again.close()


def test_monto_real_cuenta_lo_invertido_en_la_posicion(monkeypatch):
    import time
    from nexus import paths
    from nexus.agent import live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    cfg = ma.load_config()
    fake = FakeMexc()
    fake.has_keys = True
    fake.book_ticker = lambda s: {"bidPrice": "1.74", "bidQty": "5", "askPrice": "1.745", "askQty": "5"}
    fake.trade_fee = lambda s: {"makerCommission": 0, "takerCommission": 0.0005}
    fake.balances = {"X": 11.49, "USDT": 9.34}                      # 20 USDT ya están en la posición
    monkeypatch.setattr(mt, "MexcTrader", lambda: fake)
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": time.time(), "expires": time.time() + 3600,
                                                           "capital_eur": 20, "symbols": ["XUSDT"],
                                                           "sin_retiro_confirmado": True})
    paths.write_json_atomic(paths.state(live.STATE_FILE), {"XUSDT": {"model": "real", "state": "long", "pos_qty": 11.49,
                                                                     "pos_cost": 19.98, "rt_qty": 11.49, "rt_cost": 19.98,
                                                                     "entry": 1.739, "qty": 11.49, "seq": 4}})
    desk = ma.build_desk(cfg, fake, 0.0, 0.05)
    bk = desk.books["XUSDT"]
    assert bk.notional == 20 and bk.state == "long" and not bk.blocked and bk.seq == 4


def test_capital_real_se_reparte_entre_los_pares_autorizados(monkeypatch):
    import time
    from nexus import paths
    from nexus.agent import live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    info2 = {"symbols": INFO["symbols"] + [INFO["symbols"][0] | {"symbol": "YUSDT", "baseAsset": "Y"}]}
    fake = FakeMexc()
    fake.has_keys = True
    fake.exchange_info = lambda s: info2
    fake.book_ticker = lambda s: {"bidPrice": "1.0", "bidQty": "5", "askPrice": "1.001", "askQty": "5"}
    fake.trade_fee = lambda s: {"makerCommission": 0, "takerCommission": 0.0005}
    monkeypatch.setattr(mt, "MexcTrader", lambda: fake)
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": time.time(), "expires": time.time() + 3600,
                                                           "capital_eur": 20, "symbols": ["XUSDT", "YUSDT"],
                                                           "sin_retiro_confirmado": True})
    desk = ma.build_desk(ma.load_config(), fake, 0.0, 0.05)
    assert sorted(desk.books) == ["XUSDT", "YUSDT"] and {b.notional for b in desk.books.values()} == {10.0}


def test_modo_auto_valida_tope_de_pares_y_no_mezcla_con_pares():
    from nexus import paths
    from nexus.agent import authorize, live
    ok = {"evidence": {"criteria_met": 2, "criteria_total": 6}}
    assert not authorize.validate(20, 7, [], True, ok, auto=2)
    assert authorize.validate(20, 7, [], True, ok, auto=4)                     # más de 3 pares
    assert authorize.validate(20, 7, ["XUSDT"], True, ok, auto=1)              # --pares y --auto juntos
    assert authorize.validate(20, 7, [], True, ok)                             # ni pares ni auto
    args = ["--capital", "20", "--dias", "7", "--auto", "2", "--calibracion"]
    assert authorize.main(args, ask=answers("SI", authorize.PHRASE), out=lambda m: None, now=1e9) == 0
    a = live.authorization(1e9 + 1)
    assert a["auto"] == 2 and a["symbols"] == [] and a["capital_eur"] == 20
    assert authorize.main(["--capital", "20", "--auto", "2", "--pares", "XUSDT", "--calibracion"],
                          ask=answers("SI", authorize.PHRASE), out=lambda m: None, now=1e9) == 2
    paths.write_json_atomic(paths.state(live.AUTH_FILE), a | {"auto": 5})       # editada a mano: más de 3
    assert live.authorization(1e9 + 1) is None


def test_modo_auto_opera_los_promovidos_y_cambia_de_par_solo_sin_posicion(monkeypatch):
    import time
    from nexus import paths
    from nexus.agent import live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    info2 = {"symbols": INFO["symbols"] + [INFO["symbols"][0] | {"symbol": "YUSDT", "baseAsset": "Y"}]}
    fake = FakeMexc()
    fake.has_keys = True
    fake.exchange_info = lambda s: info2
    fake.book_ticker = lambda s: {"bidPrice": "1.0", "bidQty": "5", "askPrice": "1.001", "askQty": "5"}
    fake.trade_fee = lambda s: {"makerCommission": 0, "takerCommission": 0.0005}
    monkeypatch.setattr(mt, "MexcTrader", lambda: fake)
    cfg = ma.load_config()
    auth = {"ts": time.time(), "expires": time.time() + 3600, "capital_eur": 20, "symbols": [], "auto": 1,
            "sin_retiro_confirmado": True}
    paths.write_json_atomic(paths.state(live.AUTH_FILE), auth)
    assert ma.build_desk(cfg, fake, 0.0, 0.05) is None                         # nada promovido: solo paper

    def promote(**sc):
        paths.write_json_atomic(paths.state("allocator.json"), {
            "promoted": {s: 1 for s in sc}, "scores": {s: {"score": v, "n": 30, "mean": v * 2} for s, v in sc.items()}})
    promote(XUSDT=0.01, YUSDT=0.005)
    desk = ma.build_desk(cfg, fake, 0.0, 0.05)
    assert list(desk.books) == ["XUSDT"] and desk.books["XUSDT"].notional == 20  # el mejor, con todo el capital
    assert ma.rotate(desk, cfg, fake, 0.0, 0.05) is desk                       # sin cambios: no se rearma
    desk.books["XUSDT"].seq = 3
    promote(XUSDT=0.001, YUSDT=0.02)                                            # ahora Y es mejor...
    desk.books["XUSDT"].state = "long"
    assert ma.rotate(desk, cfg, fake, 0.0, 0.05) is desk                       # ...pero X está comprado: sigue
    assert not desk.books["XUSDT"].entry_allowed()                             # y no vuelve a entrar en X
    desk.books["XUSDT"].state = "flat"
    new = ma.rotate(desk, cfg, fake, 0.0, 0.05)
    assert list(new.books) == ["YUSDT"] and new.books["YUSDT"].entry_allowed()
    new.save()
    saved = json.loads(paths.state(live.STATE_FILE).read_text())
    assert saved["XUSDT"]["seq"] == 3 and "YUSDT" in saved                    # R-06: el contador de X no se pierde
    paths.write_json_atomic(paths.state(live.AUTH_FILE), auth | {"auto": 2})    # 2 pares: se reparte el capital
    two = ma.rotate(new, cfg, fake, 0.0, 0.05)
    assert sorted(two.books) == ["XUSDT", "YUSDT"] and {b.notional for b in two.books.values()} == {10.0}
    assert two.books["XUSDT"].seq == 3
    promote(YUSDT=0.02)                                                         # promovido uno solo: todo el capital
    one = ma.rotate(two, cfg, fake, 0.0, 0.05)
    assert list(one.books) == ["YUSDT"] and one.books["YUSDT"].notional == 20
    paths.state(live.AUTH_FILE).unlink()                                        # sin autorización y sin posición:
    assert ma.rotate(one, cfg, fake, 0.0, 0.05) is None                        # vuelve a solo paper


def test_el_real_ve_el_libro_sin_su_propia_orden(env):
    """bookTicker incluye nuestra orden: si los demás se iban del nivel, el real se quedaba solo arriba
    y lo llenaban justo antes de una baja. Con el libro sin nuestra orden se corre como el paper."""
    ex, bk = env["ex"], env["make"]()
    bk.on_book(1.0000, 500, 1.0010, 500)                         # compra 20 en 1,0000
    assert bk.order["cid"] == "nx1XUSDT"
    solos = {"bids": [["1.0000", "20.00"], ["0.9990", "300"]], "asks": [["1.0010", "500"]]}
    assert bk.market(solos) == (0.999, 300.0, 1.001, 500.0)     # en 1,0000 quedamos solos: el bid real es 0,9990
    con_otros = {"bids": [["1.0000", "50"]], "asks": [["1.0010", "500"]]}
    assert bk.market(con_otros)[:2] == (1.0, 30.0)               # 50 visibles = 30 de otros + nuestros 20
    sin_reflejar = {"bids": [["1.0000", "5"]], "asks": [["1.0010", "500"]]}
    assert bk.market(sin_reflejar)[:2] == (1.0, 5.0)             # la foto aún no tiene nuestra orden
    bk.on_book(*bk.market(solos))                                # solos arriba: se baja con el mercado
    assert ex.orders["nx1XUSDT"]["status"] == "CANCELED" and bk.order["px"] == 0.999
    ex.fill(bk.order["cid"], float(ex.orders[bk.order["cid"]]["origQty"]))
    bk.on_book(0.999, 300, 1.001, 500)
    bk.on_book(0.999, 300, 1.001, 500)                           # venta en el ask
    assert bk.order["side"] == "SELL" and bk.order["px"] == 1.001
    arriba = {"bids": [["0.9990", "300"]], "asks": [["1.0010", str(bk.order["qty"])], ["1.0015", "40"]]}
    assert bk.market(arriba)[2:] == (1.0015, 40.0)               # solos en el ask: el mercado vende más arriba
    bk.on_book(*bk.market(arriba))
    assert bk.order["px"] == 1.0015                              # sube como el paper (nunca bajo el piso)


def test_desk_pide_el_libro_y_descuenta_la_orden_propia(env):
    ex, bk, live = env["ex"], env["make"](), env["live"]
    pedidos = []

    class Datos:
        def _get(self, path, params=None, signed=False):
            pedidos.append((path, params))
            mine = sum(float(o["origQty"]) for o in ex.orders.values() if o["status"] == "NEW" and o["side"] == "BUY")
            return {"bids": [["0.9990", str(300 + mine)]], "asks": [["1.0010", "500"]]}
    desk = live.Desk(ex, Datos(), env["gate"], {"XUSDT": bk}, bk.olog)
    desk.step(20)
    assert pedidos[0] == ("/api/v3/depth", {"symbol": "XUSDT", "limit": live.DEPTH_LEVELS})
    assert bk.order["px"] == 0.999                               # primera cotización en el mejor bid
    desk.step(20)                                                # el nivel muestra 300 de otros + la nuestra:
    assert ex.open_cids() == ["nx1XUSDT"] and bk.bq == 300       # no se mueve y la cola visible es la de otros


def test_todo_el_capital_sigue_al_saldo_y_se_reparte(monkeypatch):
    """Pedido del dueño (25-oct): invertir siempre todo el capital disponible. --todo autoriza hasta
    100 € y el monto por par sigue al saldo real (ganancias reinvertidas), sin pasar el máximo por
    posición ni cambiar a mitad de una vuelta."""
    import time
    from nexus import paths
    from nexus.agent import authorize, live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    out = []
    assert authorize.main(["--todo", "--dias", "7", "--pares", "XUSDT", "--calibracion"],
                          ask=answers("SI", authorize.PHRASE), out=out.append, now=time.time()) == 0
    a = live.authorization()
    assert a["todo"] and a["capital_eur"] == 100 and any("TODO el capital" in m for m in out)
    info2 = {"symbols": INFO["symbols"] + [INFO["symbols"][0] | {"symbol": "YUSDT", "baseAsset": "Y"}]}
    fake = FakeMexc()
    fake.has_keys = True
    fake.exchange_info = lambda s: info2
    fake.book_ticker = lambda s: {"bidPrice": "1.0", "bidQty": "5", "askPrice": "1.001", "askQty": "5"}
    fake.trade_fee = lambda s: {"makerCommission": 0, "takerCommission": 0.0005}
    fake.balances = {"USDT": 29.0}
    monkeypatch.setattr(mt, "MexcTrader", lambda: fake)
    cfg = ma.load_config()
    desk = ma.build_desk(cfg, fake, 0.0, 0.05)
    assert desk.books["XUSDT"].notional == 28.42                               # todo lo que hay, con 2% de margen
    fake.balances["USDT"] = 35.0                                                # ganó: se reinvierte
    desk.books["XUSDT"].state = "long"
    ma.resize(desk, cfg, desk.check_balances())
    assert desk.books["XUSDT"].notional == 28.42                               # a mitad de una vuelta no cambia
    desk.books["XUSDT"].state = "flat"
    ma.resize(desk, cfg, desk.check_balances())
    assert desk.books["XUSDT"].notional == 34.3
    fake.balances["USDT"] = 500.0
    ma.resize(desk, cfg, desk.check_balances())
    assert desk.books["XUSDT"].notional == cfg["risk"]["max_position_eur"]      # nunca más que el máximo por posición
    paths.write_json_atomic(paths.state(live.AUTH_FILE), a | {"symbols": ["XUSDT", "YUSDT"]})
    fake.balances["USDT"] = 29.0
    two = ma.build_desk(cfg, fake, 0.0, 0.05)
    assert {b.notional for b in two.books.values()} == {14.21}                  # dos pares: la mitad cada uno


def test_preflight_con_todo_no_exige_el_tope_en_la_cuenta():
    import time
    from nexus import paths, preflight
    from nexus.agent import live
    from tests.test_comms import FakeClient
    now = time.time()
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": now, "expires": now + 3600, "capital_eur": 100,
                                                           "todo": True, "symbols": ["XUSDT"]})
    r = preflight.run(FakeClient(), clock=lambda n: 100)
    assert r["ready"] and r["account"]["capital_usdt"] == 21.5                  # 21,5 USDT: se opera lo que hay

    class Vacia(FakeClient):
        def account(self):
            return {"canTrade": True, "canWithdraw": False, "balances": [{"asset": "USDT", "free": "3", "locked": "0"}]}
    r = preflight.run(Vacia(), clock=lambda n: 100)
    assert not r["ready"] and "no alcanza para operar" in r["problems"][0]
