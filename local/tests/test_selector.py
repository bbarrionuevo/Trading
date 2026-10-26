"""Selector del dinero real: opera la combinación (par, variante) que hoy gana y rota sola al perder."""
import json
import time

NOW = 1_800_000_000
VIG = "v3-vol0.3"
ALLOWED = {VIG, "control", "v4-imb0.6", "v5-tick1"}


def _p(sym, strategy, pnls, t0=NOW - 3600):
    return [{"ts": t0 + i, "sym": sym, "strategy": strategy, "pnl": x, "notional": 40.0} for i, x in enumerate(pnls)]


def test_puntajes_y_eleccion_por_combinacion():
    from nexus import selector
    tr = (_p("A", VIG, [0.04, 0.05] * 10) + _p("A", "v5-tick1", [0.08, 0.07] * 10)      # en A gana más la v5
          + _p("B", VIG, [0.03, 0.02] * 10) + _p("C", VIG, [0.1, -0.4] * 10)            # C: media negativa
          + _p("D", VIG, [0.2] * 5) + _p("E", VIG, [0.05] * 20, t0=NOW - 4 * 86400))     # D pocas; E vieja
    sc = selector.combo_scores(tr, NOW)
    assert sc["A|v5-tick1"]["score"] > sc["A|" + VIG]["score"] > 0 and sc["C|" + VIG]["score"] < 0
    assert sc["D|" + VIG]["score"] is None and "E|" + VIG not in sc
    assert selector.pick(sc, {"auto": 2}, {}, VIG, ALLOWED) == {"A": "v5-tick1", "B": VIG}   # una variante por par
    assert selector.pick(sc, {"auto": 1}, {"A|v5-tick1": NOW + 1}, VIG, ALLOWED) == {"A": VIG}
    assert selector.pick(sc, {"auto": 2}, {}, VIG, ALLOWED - {"v5-tick1"}) == {"A": VIG, "B": VIG}
    assert selector.pick(sc, {"symbols": ["C", "A"]}, {}, VIG, ALLOWED) == {"C": VIG, "A": "v5-tick1"}  # sin evidencia: vigente
    assert selector.pick(sc, {"symbols": ["C"]}, {"C|" + VIG: NOW + 1}, VIG, ALLOWED) == {}


def test_rota_si_el_real_pierde_el_2_por_ciento_o_diverge_del_paper():
    from nexus import selector
    tr = _p("A", VIG, [0.05, 0.04] * 10) + _p("B", VIG, [0.03, 0.02] * 10)
    sc = selector.combo_scores(tr, NOW)
    st = selector.update({}, sc, {"auto": 1}, NOW, [], tr, 30.0, VIG, ALLOWED)
    assert st["choices"] == {"A": {"strategy": VIG, "since": NOW}} and st["history"][-1]["event"] == "elegida"
    real = [{"ts": NOW + 10, "sym": "A", "pnl": -0.35, "notional": 30}, {"ts": NOW + 20, "sym": "A", "pnl": -0.30, "notional": 30}]
    st = selector.update(st, sc, {"auto": 1}, NOW + 30, real, tr, 30.0, VIG, ALLOWED)
    assert st["choices"] == {"B": {"strategy": VIG, "since": NOW + 30}}              # perdió 0,65 > 2% de 30
    assert st["banned"]["A|*"] == NOW + 30 + 24 * 3600 and "2% del capital" in st["history"][-2]["why"]
    real_b = [{"ts": NOW + 40 + i, "sym": "B", "pnl": -0.003, "notional": 30} for i in range(10)]
    paper_b = _p("B", VIG, [0.05] * 3, t0=NOW + 40)
    st = selector.update(st, sc, {"auto": 1}, NOW + 60, real_b, tr + paper_b, 30.0, VIG, ALLOWED)
    assert st["choices"] == {} and "paper de la misma combinación gana" in st["history"][-1]["why"]
    st = selector.update(st, sc, {"auto": 1}, NOW + 25 * 3600, [], tr, 30.0, VIG, ALLOWED)
    assert st["banned"] == {} and list(st["choices"]) == ["A"]                         # pasadas 24 h vuelven


def test_agente_opera_la_variante_elegida_y_rota_con_la_compra_puesta(monkeypatch):
    from nexus import evidence, paths
    from nexus.agent import live
    import nexus.agent.maker_agent as ma
    import nexus.exchanges.mexc_trade as mt
    from tests.test_live import INFO, FakeMexc
    info2 = {"symbols": INFO["symbols"] + [INFO["symbols"][0] | {"symbol": "YUSDT", "baseAsset": "Y"}]}
    fake = FakeMexc()
    fake.has_keys = True
    fake.exchange_info = lambda s: info2
    fake.book_ticker = lambda s: {"bidPrice": "1.0", "bidQty": "5", "askPrice": "1.010", "askQty": "5"}
    fake.trade_fee = lambda s: {"makerCommission": 0, "takerCommission": 0.0005}
    monkeypatch.setattr(mt, "MexcTrader", lambda: fake)
    cfg = ma.load_config() | {"max_vol_1m_pct": None}
    ma.STRATEGY["tag"] = cfg["strategy_tag"]
    now = time.time()
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": now, "expires": now + 3600, "capital_eur": 100,
                                                           "todo": True, "symbols": [], "auto": 1,
                                                           "sin_retiro_confirmado": True})
    with open(paths.state("trades.jsonl"), "w") as f:
        for t in _p("XUSDT", "v5-tick1", [0.08, 0.07] * 10, t0=now - 3600) + _p("YUSDT", VIG, [0.03, 0.02] * 10, t0=now - 3600):
            f.write(json.dumps(t | {"model": evidence.FILL_MODEL}) + "\n")
    st = ma.selector_tick(cfg, None)
    assert ma.real_choices() == {"XUSDT": "v5-tick1"} and st["top"][0]["sym"] == "XUSDT"
    desk = ma.build_desk(cfg, fake, 0.0, 0.05)
    bk = desk.books["XUSDT"]
    assert bk.strategy == "v5-tick1" and bk.improve == 1 and bk.dp == 4 and bk.entry_allowed()
    for _ in range(11):                                          # la v5 también filtra volatilidad: junta datos
        desk.step(20)
    assert bk.order["px"] == 1.0001                              # v5 en real: un tick arriba del bid
    sel = json.loads(paths.state("selector.json").read_text())
    sel["choices"] = {"YUSDT": {"strategy": VIG, "since": now}}  # el selector cambia de combinación
    paths.write_json_atomic(paths.state("selector.json"), sel)
    assert not bk.entry_allowed()
    new = ma.rotate(desk, cfg, fake, 0.0, 0.05)                  # flat con la compra puesta: rota igual
    assert list(new.books) == ["YUSDT"] and new.books["YUSDT"].strategy == VIG and fake.open_cids() == []
    new.books["YUSDT"].on_closed({"ts": now, "sym": "YUSDT", "pnl": 0.01})
    assert json.loads(paths.state(live.REAL_TRADES).read_text().splitlines()[-1])["strategy"] == VIG


def test_latido_e_informe_muestran_el_selector():
    from nexus import paths
    from nexus.monitor import digest
    from tests.test_comms import _rep
    paths.write_json_atomic(paths.state("selector.json"), {
        "ts": NOW, "choices": {"XUSDT": {"strategy": "v5-tick1", "since": NOW}}, "banned": {"YUSDT|v3-vol0.3": NOW + 9},
        "top": [], "history": [{"ts": NOW, "event": "elegida"}] * 30})
    b = _rep().build()
    assert b["selector"]["choices"]["XUSDT"]["strategy"] == "v5-tick1" and len(b["selector"]["history"]) == 10
    st = {"ts": NOW, "books": [], "live": {"auth": {"capital_eur": 100, "todo": True, "auto": 1}, "books": []}}
    md = digest({"status": st, "selector": b["selector"]}, None, now=NOW)
    assert "Selector: opera XUSDT (v5-tick1); descartadas: YUSDT|v3-vol0.3." in md


def test_al_descartar_cambia_de_par_y_no_a_otra_variante_del_mismo():
    """26-oct: ZRO vigente perdió y el selector pasó a ZRO control, que en ZRO operaba igual."""
    from nexus import selector
    tr = (_p("A", VIG, [0.05, 0.04] * 10) + _p("A", "control", [0.05, 0.04] * 10)
          + _p("B", VIG, [0.03, 0.02] * 10))
    sc = selector.combo_scores(tr, NOW)
    st = selector.update({}, sc, {"auto": 1}, NOW, [], tr, 30.0, VIG, ALLOWED)
    assert list(st["choices"]) == ["A"]
    real = [{"ts": NOW + 10, "sym": "A", "pnl": -0.7, "notional": 30}]
    st = selector.update(st, sc, {"auto": 1}, NOW + 20, real, tr, 30.0, VIG, ALLOWED)
    assert st["choices"] == {"B": {"strategy": VIG, "since": NOW + 20}}          # no A control: otro par
    assert selector.pick(sc, {"symbols": ["A"]}, st["banned"], VIG, ALLOWED) == {}   # con pares fijos, espera


def test_par_bloqueado_sin_posicion_se_deja_y_se_elige_otro():
    from nexus import selector
    tr = _p("A", VIG, [0.05, 0.04] * 10) + _p("B", VIG, [0.03, 0.02] * 10)
    sc = selector.combo_scores(tr, NOW)
    st = selector.update({}, sc, {"auto": 1}, NOW, [], tr, 30.0, VIG, ALLOWED)
    st = selector.update(st, sc, {"auto": 1}, NOW + 60, [], tr, 30.0, VIG, ALLOWED, blocked=["A"])
    assert list(st["choices"]) == ["B"] and "A|*" in st["banned"] and "bloqueado" in st["history"][-2]["why"]
