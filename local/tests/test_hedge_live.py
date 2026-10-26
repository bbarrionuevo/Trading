"""Cobertura MEXC→Binance con dinero real, contra exchanges simulados: cotiza solo con margen, cubre
cada llenado al instante, concilia cortes de red, se bloquea si no puede cubrir y respeta el KILL."""
import json

import pytest

from tests.test_live import INFO, Clock, FakeMexc

BINFO = {"symbols": [{"symbol": "XUSDT", "status": "TRADING", "baseAsset": "X", "orderTypes": ["LIMIT", "MARKET"],
                      "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
                                  {"filterType": "NOTIONAL", "minNotional": "5"}]}]}


class FakeBinance:
    def __init__(self):
        from nexus.exchanges.mexc_trade import OrderRejected
        self.Rej = OrderRejected
        self.orders, self.calls, self.fail, self.reject = {}, [], {}, {}
        self.bid, self.ask = 1.000, 1.001
        self.balances = {"X": 100.0, "USDT": 100.0}

    def new_order(self, symbol, side, type_, quantity, price=None, cid=None):
        self.calls.append(("new", side, type_, quantity, cid))
        q = float(quantity)
        px = self.bid if side == "SELL" else self.ask
        self.orders[cid] = {"clientOrderId": cid, "status": "FILLED", "executedQty": quantity,
                            "cummulativeQuoteQty": f"{q * px}"}
        if self.fail.get("new"):                         # llegó y se ejecutó, pero la respuesta se perdió
            self.fail["new"] -= 1
            raise ConnectionError("corte de red")
        if self.reject.get("new"):
            self.reject["new"] -= 1
            del self.orders[cid]
            raise self.Rej(400, -2010, "Account has insufficient balance")
        return dict(self.orders[cid])

    def get_order(self, symbol, cid):
        if cid not in self.orders:
            raise self.Rej(400, -2013, "Order does not exist.")
        return dict(self.orders[cid])

    def account(self):
        return {"balances": [{"asset": a, "free": str(v), "locked": "0"} for a, v in self.balances.items()]}

    def book_ticker(self, s):
        return {"bidPrice": str(self.bid), "askPrice": str(self.ask)}


@pytest.fixture
def h(tmp_path):
    from nexus.agent import hedge_live, live
    m, b, clock, trades = FakeMexc(), FakeBinance(), Clock(), []
    m.balances = {"X": 100.0, "USDT": 100.0}
    pair = hedge_live.HedgePair("XUSDT", m, b, live.Rules(INFO, "XUSDT"), hedge_live.BinanceRules(BINFO, "XUSDT"),
                                live.OrderLog(tmp_path / "ho.jsonl"), lot_usdt=10, min_edge_pct=0.1, taker_b_pct=0.1,
                                now=clock, on_trade=trades.append)
    pair.balances(m.account()["balances"], b.account()["balances"])
    return {"m": m, "b": b, "p": pair, "clock": clock, "trades": trades, "hl": hedge_live}


BOOK = {"bids": [["0.9900", "500"]], "asks": [["1.0100", "500"]]}


def test_cotiza_ambos_lados_solo_si_la_cobertura_deja_margen(h):
    m, b, p = h["m"], h["b"], h["p"]
    p.step(BOOK, b.bid, b.ask)
    assert {o["side"]: o["price"] for o in m.orders.values()} == {"BUY": "0.9900", "SELL": "1.0100"}
    assert all(o["type"] == "LIMIT_MAKER" for o in m.orders.values())
    p.step({"bids": [["0.9995", "500"]], "asks": [["1.0010", "500"]]}, b.bid, b.ask)   # sin margen: retira
    assert m.open_cids() == []


def test_llenado_en_mexc_se_cubre_al_instante_en_binance(h):
    m, b, p, trades = h["m"], h["b"], h["p"], h["trades"]
    p.step(BOOK, b.bid, b.ask)
    buy = next(c for c, o in m.orders.items() if o["side"] == "BUY")
    m.fill(buy, 10.1)                                    # alguien vende en MEXC a 0,99
    p.step(BOOK, b.bid, b.ask)
    assert b.calls[0][:4] == ("new", "SELL", "MARKET", "10.10")          # vende lo mismo en Binance
    t = trades[0]
    assert t["side"] == "BUY" and t["pnl"] == pytest.approx(10.1 * (1.000 - 0.99) - 10.1 * 0.001)
    assert p.pending["BUY"][0] == 0 and p.fills == 1 and p.inv["b_coin"] == pytest.approx(89.9)
    assert len([o for o in m.orders.values() if o["side"] == "BUY" and o["status"] == "NEW"]) == 1   # vuelve a cotizar


def test_migaja_espera_al_proximo_llenado_y_corte_de_red_se_concilia(h):
    m, b, p, clock = h["m"], h["b"], h["p"], h["clock"]
    p.step(BOOK, b.bid, b.ask)
    buy = next(c for c, o in m.orders.items() if o["side"] == "BUY")
    m.fill(buy, 3.0)                                     # 2,97 USDT: menos que el mínimo de Binance (5)
    p.step(BOOK, b.bid, b.ask)
    assert b.calls == [] and p.pending["BUY"][0] == 3.0
    m.fill(buy, 7.1)
    b.fail["new"] = 1                                    # la cobertura se ejecuta pero no llega la respuesta
    p.step(BOOK, b.bid, b.ask)
    assert p.hedge and len(b.calls) == 1 and m.open_cids() == []        # no cotiza mientras no sabe
    p.step(BOOK, b.bid, b.ask)                           # concilia: estaba ejecutada, no se repite
    assert p.hedge is None and len(b.calls) == 1 and p.fills == 1 and p.pending["BUY"][0] == 0
    assert json.loads((p.olog.path).read_text().splitlines()[-1])  # diario con hash


def test_cobertura_rechazada_bloquea_el_par_y_cancela_mexc(h):
    m, b, p, clock = h["m"], h["b"], h["p"], h["clock"]
    p.step(BOOK, b.bid, b.ask)
    buy = next(c for c, o in m.orders.items() if o["side"] == "BUY")
    m.fill(buy, 10.1)
    b.reject["new"] = 3
    p.step(BOOK, b.bid, b.ask)
    assert m.open_cids() == []                            # con moneda sin cubrir no cotiza más
    for _ in range(2):
        p.step(BOOK, b.bid, b.ask)
    assert p.blocked and "3 rechazos seguidos en Binance" in p.blocked and m.open_cids() == []
    p.step(BOOK, b.bid, b.ask)
    assert m.open_cids() == []                            # bloqueado: no vuelve a cotizar solo


def test_sin_cubrir_mucho_tiempo_bloquea(h):
    m, b, p, clock = h["m"], h["b"], h["p"], h["clock"]
    p.step(BOOK, b.bid, b.ask)
    buy = next(c for c, o in m.orders.items() if o["side"] == "BUY")
    m.fill(buy, 10.1)
    b.fail["new"] = 1
    p.step(BOOK, b.bid, b.ask)                            # resultado desconocido
    del b.orders[p.hedge["cid"]]                          # ...y en realidad Binance nunca la tuvo
    b.get_order = lambda s, c: (_ for _ in ()).throw(ConnectionError())   # y Binance no responde
    clock.t += 31
    p.step(BOOK, b.bid, b.ask)
    assert p.blocked and "sin cubrir" in p.blocked


def test_kill_y_falta_de_saldo_dejan_de_cotizar(h):
    from nexus import paths
    m, b, p = h["m"], h["b"], h["p"]
    paths.kill_file().write_text("1")
    p.step(BOOK, b.bid, b.ask)
    assert m.open_cids() == []
    paths.kill_file().unlink()
    b.balances["X"] = 0.0                                 # sin moneda en Binance no se puede cubrir una compra
    p.balances(m.account()["balances"], b.account()["balances"])
    p.step(BOOK, b.bid, b.ask)
    assert [o["side"] for o in m.orders.values() if o["status"] == "NEW"] == ["SELL"]


def test_ve_el_libro_de_mexc_sin_sus_ordenes(h):
    m, b, p = h["m"], h["b"], h["p"]
    p.step(BOOK, b.bid, b.ask)
    own = {o["side"]: o["origQty"] for o in m.orders.values()}
    solo = {"bids": [["0.9900", own["BUY"]], ["0.9890", "300"]], "asks": [["1.0100", own["SELL"]], ["1.0110", "300"]]}
    assert p.market(solo) == (0.989, 1.011)


def test_al_arrancar_cancela_huerfanas_y_cubre_lo_pendiente(h):
    m, b, p = h["m"], h["b"], h["p"]
    p.step(BOOK, b.bid, b.ask)
    saved = json.loads(json.dumps(p.to_dict()))
    buy = next(c for c, o in m.orders.items() if o["side"] == "BUY")
    m.fill(buy, 10.1)                                     # se llenó mientras la PC estaba apagada
    q = h["hl"].HedgePair("XUSDT", m, b, p.mr, p.br, p.olog, lot_usdt=10, now=h["clock"], on_trade=h["trades"].append)
    q.load(saved)
    q.balances(m.account()["balances"], b.account()["balances"])
    q.reconcile()
    assert m.open_cids() == [] and q.pending["BUY"][0] == 10.1
    q.step(BOOK, b.bid, b.ask)
    assert b.calls[-1][:2] == ("new", "SELL") and q.pending["BUY"][0] == 0


def test_cliente_de_binance_solo_endpoints_de_ordenes():
    from nexus.exchanges.base import ReadOnlyViolation
    from nexus.exchanges.binance_trade import BinanceTrader
    from nexus.exchanges.mexc_trade import OrderRejected
    t = BinanceTrader("k", "s")
    for method, path in (("POST", "/sapi/v1/capital/withdraw/apply"), ("POST", "/sapi/v1/asset/transfer"),
                         ("POST", "/sapi/v1/margin/order"), ("DELETE", "/api/v3/openOrders")):
        with pytest.raises(ReadOnlyViolation):
            t._send(method, path, {})

    class R:
        status_code = 400
        text = ""

        def json(self):
            return {"code": -2013, "msg": "Order does not exist."}
    seen = {}
    t.s.request = lambda method, url, params=None, headers=None, timeout=None: seen.update(params=params) or R()
    with pytest.raises(OrderRejected) as e:
        t.new_order("XUSDT", "SELL", "MARKET", "10.10", None, "hb1XUSDT")
    assert e.value.not_found and seen["params"]["newOrderRespType"] == "FULL" and "timeInForce" not in seen["params"]


def _answers(*xs):
    it = iter(xs)
    return lambda prompt: next(it)


def test_autorizacion_de_la_cobertura_topes_y_revocar():
    import time
    from nexus import paths
    from nexus.agent import authorize, hedge_live, live
    now = time.time()
    out = []
    assert authorize.main(["--cobertura", "QUICKUSDT", "--lote", "30", "--calibracion"], ask=_answers(), out=out.append,
                          now=now) == 2 and "fuera de rango" in out[-1]
    assert authorize.main(["--cobertura", "AUSDT,BUSDT,CUSDT,DUSDT", "--calibracion"], ask=_answers(), out=out.append,
                          now=now) == 2
    assert authorize.main(["--cobertura", "QUICKUSDT"], ask=_answers(), out=out.append, now=now) == 2   # sin --calibracion
    assert authorize.main(["--cobertura", "QUICKUSDT", "--calibracion"], ask=_answers("no"), out=out.append, now=now) == 2
    assert hedge_live.hedge_authorization(now + 1) is None
    assert authorize.main(["--cobertura", "QUICKUSDT,DCRUSDT", "--lote", "10", "--calibracion"],
                          ask=_answers("SI", authorize.PHRASE), out=out.append, now=now) == 0
    a = hedge_live.hedge_authorization(now + 1)
    assert a["symbols"] == ["QUICKUSDT", "DCRUSDT"] and a["lot_usdt"] == 10 and live.authorization(now + 1) is None
    authorize.main(["--estado"], out=out.append, now=now + 2)
    assert any("Cobertura vigente" in m for m in out)
    paths.write_json_atomic(paths.state(hedge_live.HEDGE_AUTH), a | {"lot_usdt": 40})   # editada a mano
    assert hedge_live.hedge_authorization(now + 1) is None
    paths.write_json_atomic(paths.state(hedge_live.HEDGE_AUTH), a)
    authorize.main(["--revocar"], out=out.append, now=now + 3)                            # revoca todo
    assert hedge_live.hedge_authorization(now + 4) is None


def test_el_agente_arma_la_cobertura_y_reserva_su_usdt(monkeypatch):
    import time
    from nexus import paths
    from nexus.agent import hedge_live, live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.binance_trade as bt
    import nexus.exchanges.mexc_trade as mt
    m, b = FakeMexc(), FakeBinance()
    m.has_keys, m.balances = True, {"X": 100.0, "USDT": 100.0}
    b.key = b.secret = "k"
    b.account = lambda: {"canTrade": True, "canWithdraw": False,
                         "balances": [{"asset": a, "free": str(v), "locked": "0"} for a, v in b.balances.items()]}
    b.exchange_info = lambda s: BINFO
    perms = {"enableReading": True, "enableSpotAndMarginTrading": True, "enableWithdrawals": True, "ipRestrict": True}
    b.api_restrictions = lambda: perms
    monkeypatch.setattr(mt, "MexcTrader", lambda: m)
    monkeypatch.setattr(bt, "BinanceTrader", lambda: b)

    class Data:
        def _get(self, path, params=None, signed=False):
            return BOOK
    cfg = ma.load_config()
    assert ma.build_hedge_desk(cfg, Data()) is None                       # sin autorización: nada
    now = time.time()
    paths.write_json_atomic(paths.state(hedge_live.HEDGE_AUTH), {"ts": now, "expires": now + 3600, "symbols": ["XUSDT"],
                                                                 "lot_usdt": 10, "sin_retiro_confirmado": True})
    assert ma.build_hedge_desk(cfg, Data()) is None                       # R-01: la clave permite retiros
    perms["enableWithdrawals"] = False
    desk = ma.build_hedge_desk(cfg, Data())
    desk.step()
    assert sorted(o["side"] for o in m.orders.values()) == ["BUY", "SELL"] and desk.status()["pairs"][0]["sym"] == "XUSDT"
    assert ma.books_snapshot(type("G", (), {"books": {}})(), None, desk)["XUSDT#cobertura"]["state"] == "flat"
    assert ma.real_notional({"capital_eur": 100, "todo": True}, cfg, [{"asset": "USDT", "free": "30"}], {}, 1) == 19.6
    desk.shutdown()
    assert m.open_cids() == [] and json.loads(paths.state(hedge_live.HEDGE_STATE).read_text())["XUSDT"]["seq"] == 2


def test_desbloqueo_humano_de_la_cobertura(h):
    from nexus import paths
    from nexus.agent import authorize, hedge_live
    m, b, p = h["m"], h["b"], h["p"]
    p.step(BOOK, b.bid, b.ask)
    p._block("prueba")
    desk = hedge_live.HedgeDesk(m, b, type("D", (), {"_get": lambda self, path, params=None: BOOK})(), {"XUSDT": p},
                                p.olog, h["trades"].append)
    desk.step()
    assert m.open_cids() == []
    assert authorize.main(["--desbloquear-cobertura", "XUSDT"], ask=_answers("no"), out=lambda x: None) == 2
    assert authorize.main(["--desbloquear-cobertura", "XUSDT"], ask=_answers("SI"), out=lambda x: None) == 0
    desk.step()
    assert p.blocked is None and not paths.state(hedge_live.HEDGE_UNBLOCK).exists()
    assert sorted(o["side"] for o in m.orders.values() if o["status"] == "NEW") == ["BUY", "SELL"]


def test_avisos_de_permisos_de_la_clave_de_binance():
    from nexus.check_keys import key_warnings
    ok = {"enableSpotAndMarginTrading": True, "ipRestrict": True}
    assert key_warnings(ok) == []
    w = key_warnings(ok | {"enableWithdrawals": True, "permitsUniversalTransfer": True, "ipRestrict": False})
    assert any("RETIROS" in x for x in w) and any("transferencias" in x for x in w) and any("IP" in x for x in w)
    assert any("trading spot" in x for x in key_warnings({"ipRestrict": True}))
