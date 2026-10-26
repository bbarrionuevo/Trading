"""Asignador de pares: puntaje con cota inferior, promociones y prueba fuera de muestra."""
import json

NOW = 1_800_000_000


def _t(sym, pnl, i=0, lab=True, strategy="v3", age_days=0.0):
    return {"ts": NOW - age_days * 86400 - 1000 + i, "sym": sym, "pnl": pnl, "lab": lab, "strategy": strategy}


def test_puntaje_castiga_pocas_vueltas_y_stops():
    from nexus.allocator import score
    assert score([0.05] * 10, min_n=20) is None                       # pocas vueltas: sin opinión
    assert score([0.05, 0.04] * 15, min_n=20) > 0                     # gana parejo
    assert score([0.05] * 25 + [-0.9] * 2, min_n=20) < 0              # 2 stops se comen 25 ganancias


def test_puntajes_solo_con_laboratorio_de_la_version_vigente_y_recientes():
    from nexus.allocator import scores
    tr = ([_t("A", 0.05 + (i % 3) * 0.01, i) for i in range(80)]
          + [_t("A", -5.0, i, age_days=8) for i in range(30)]         # viejo: fuera de la ventana de días
          + [_t("B", 0.05, i, strategy="control") for i in range(30)]  # control: otra estrategia
          + [_t("C", 0.05, i, lab=False) for i in range(30)])          # libro operado: no es laboratorio
    sc = scores(tr, "v3", NOW, window=60, min_n=20, days=7)
    assert set(sc) == {"A"} and sc["A"]["n"] == 60 and sc["A"]["score"] > 0


def test_promueve_descarta_y_conserva_los_mejores():
    from nexus.allocator import decide, status
    sc = {"A": {"score": 0.02, "n": 40}, "B": {"score": 0.05, "n": 40}, "C": {"score": -0.01, "n": 40},
          "D": {"score": None, "n": 5}, "E": {"score": 0.03, "n": 40}}
    st, keep = decide({}, sc, NOW, keep_max=2, exclude={"E"})
    assert keep == ["B", "A"]                                         # mejor puntaje primero; E pausado
    assert set(st["promoted"]) == {"A", "B", "E"} and status(st, "B") == "promovido" and status(st, "C") == "explorando"
    st2, keep2 = decide(st, {"A": {"score": -0.001, "n": 60}, "B": {"score": 0.04, "n": 60}}, NOW + 6 * 3600)
    assert keep2 == ["B"] and set(st2["promoted"]) == {"B"}           # A dejó de ganar; E sin vueltas recientes
    assert st2["promoted"]["B"] == NOW                                # conserva la fecha de promoción
    assert [h["event"] for h in st2["history"] if h["sym"] == "A"] == ["promovido", "descartado"]


def test_laboratorio_conserva_promovidos_y_marca_cada_vuelta(tmp_path, monkeypatch):
    from nexus import evidence, paths
    from nexus.agent.maker_agent import Lab
    from tests.test_windows import LAB, FakeMexc
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path))
    hist = [dict(_t("ZZZUSDT", 0.05 + (i % 2) * 0.01, i, strategy=None), ts=1e9 + i, model=evidence.FILL_MODEL)
            for i in range(30)]
    paths.state("trades.jsonl").write_text("\n".join(json.dumps(t) for t in hist) + "\n")
    cfg = {"symbols": ["BONKUSDT"], "lab": LAB | {"max_symbols": 2, "score_days": 1e6},
           "stop_pct": 1.0, "min_edge_pct": 0.0, "max_hold_min": 30, "risk": {"max_position_eur": 40}}
    lab = Lab(FakeMexc(), cfg, 0.0, 0.08)
    for _ in range(8):
        lab.step()
    assert list(lab.books) == ["ZZZUSDT", "AAAUSDT"]                  # 1 promovido + 1 explorando
    st = {b["sym"]: b for b in lab.status()}
    assert st["ZZZUSDT"]["sel"] == "promovido" and st["ZZZUSDT"]["score"] > 0 and st["AAAUSDT"]["sel"] == "explorando"
    new = [json.loads(x) for x in paths.state("trades.jsonl").read_text().splitlines()][30:]
    sels = {(t["sym"], t["sel"]) for t in new if t.get("strategy") is None}
    assert ("ZZZUSDT", "promovido") in sels and ("AAAUSDT", "explorando") in sels
    ev = evidence.evaluate(paths.state("trades.jsonl"))
    assert set(ev["selection"]) >= {"promovido", "explorando", "sin dato"}
    assert json.loads(paths.state("allocator.json").read_text())["promoted"] == {"ZZZUSDT": lab.alloc["ts"]}


def test_informe_muestra_la_seleccion_fuera_de_muestra():
    from nexus.monitor import digest
    ev = {"selection": {"explorando": {"n": 40, "total": -1.2, "mean": -0.03, "ci95_low": -0.06},
                        "promovido": {"n": 30, "total": 0.9, "mean": 0.03, "ci95_low": 0.01}}}
    md = digest({"status": {"evidence": ev, "books": []}}, None, now=NOW)
    assert "fuera de muestra" in md and "| promovido | 30 | +0.90 |" in md


def test_recomienda_promovidos_por_cota_inferior():
    from nexus.allocator import recommend
    st = {"promoted": {"A": NOW, "B": NOW, "C": NOW},
          "scores": {"A": {"score": 0.01, "n": 30, "mean": 0.03}, "B": {"score": 0.02, "n": 25, "mean": 0.04},
                     "C": {"score": None, "n": 3, "mean": 0.1}, "D": {"score": 0.5, "n": 40, "mean": 0.6}}}
    assert [x["sym"] for x in recommend(st)] == ["B", "A"]                    # D no está promovido; C sin cota


def test_calibracion_compara_paper_y_real_del_mismo_par(tmp_path):
    import nexus.agent.maker_agent as ma
    from nexus import evidence
    ma.STRATEGY["tag"] = "v3"
    tp, rp = tmp_path / "t.jsonl", tmp_path / "r.jsonl"
    m = evidence.FILL_MODEL
    tp.write_text("\n".join(json.dumps(t) for t in [
        {"ts": NOW - 7200, "sym": "X", "pnl": 9, "model": m, "strategy": "v3"},          # antes de autorizar
        {"ts": NOW - 100, "sym": "X", "pnl": 0.05, "model": m, "strategy": "v3"},
        {"ts": NOW - 50, "sym": "X", "pnl": 0.03, "model": m, "strategy": "v3"},
        {"ts": NOW - 40, "sym": "Y", "pnl": 0.03, "model": m, "strategy": "v3"}]))
    rp.write_text(json.dumps({"ts": NOW - 30, "sym": "X", "pnl": -0.02, "real": True}) + "\n")
    c = ma.calibration({"ts": NOW - 3600, "symbols": ["X"]}, tp, rp, now=NOW)
    assert c["X"]["paper"]["n"] == 2 and c["X"]["paper"]["per_hour"] == 2.0
    assert c["X"]["real"]["n"] == 1 and c["X"]["real"]["total"] == -0.05            # -0,02 € sobre 40 € = -0,05%
    assert c["X"]["paper"]["mean"] == 0.1                                            # (0,05 + 0,03) / 2 / 40 € en %
    assert ma.calibration(None) is None
    ma.STRATEGY["tag"] = None


def test_conserva_pares_prometedores_hasta_juntar_evidencia():
    from nexus.allocator import decide
    sc = {"A": {"score": 0.02, "n": 40, "mean": 0.03}, "B": {"score": None, "n": 11, "mean": 0.12},
          "C": {"score": None, "n": 4, "mean": 0.2}, "D": {"score": None, "n": 8, "mean": -0.01},
          "E": {"score": None, "n": 9, "mean": 0.05}, "F": {"score": None, "n": 6, "mean": 0.3}}
    st, keep = decide({}, sc, NOW, keep_max=6, exclude={"F"}, eval_max=3, eval_min_n=5)
    assert keep == ["A", "B", "E"] and st["evaluating"] == ["B", "E"] and set(st["promoted"]) == {"A"}


def test_filtro_de_desbalance_del_libro():
    from nexus.live_paper_maker import Book
    b = Book("X", notional=10, min_imbalance=0.6)
    b.on_book(1.0, 10, 1.001, 90)                              # 10% del tamaño compra: no entra
    assert b.px is None
    b.on_book(1.0, 70, 1.001, 30)                              # 70%: entra al bid
    assert b.px == 1.0
    b.on_book(1.0, 20, 1.001, 80)                              # vuelve a dominar la venta: cancela
    assert b.px is None


def test_variantes_del_laboratorio_y_su_comparacion(tmp_path, monkeypatch):
    from nexus import evidence, paths
    from nexus.agent.maker_agent import Lab
    from tests.test_windows import LAB, FakeMexc
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path))
    cfg = {"symbols": ["BONKUSDT"], "lab": LAB | {"variants": {"v4": {"max_vol_1m_pct": None, "min_imbalance": 0.0},
                                                               "v5": {"max_vol_1m_pct": None, "improve_ticks": 1}}},
           "stop_pct": 1.0, "min_edge_pct": 0.0, "max_hold_min": 30, "risk": {"max_position_eur": 40}}

    class ConDecimales(FakeMexc):
        def _get(self, path, params=None, signed=False):
            if path == "/api/v3/exchangeInfo":
                return {"symbols": [{"symbol": params["symbol"], "quotePrecision": 3}]}
            return super()._get(path, params, signed)
    lab = Lab(ConDecimales(), cfg, 0.0, 0.08)
    for _ in range(8):
        lab.step()
    lab.save()
    keys = set(json.loads(paths.state("lab_books.json").read_text()))
    assert {"AAAUSDT", "AAAUSDT#control", "AAAUSDT#v4", "AAAUSDT#v5"} <= keys
    rows = [json.loads(x) for x in paths.state("trades.jsonl").read_text().splitlines()]
    assert {"control", "v4", "v5"} <= {r["strategy"] for r in rows}
    v5 = [r for r in rows if r["strategy"] == "v5"]
    assert v5[0]["entry"] == 1.001 and v5[0]["exit"] == 1.002                   # un tick adentro de cada lado
    assert lab.dp["AAAUSDT"] == 3                                               # exchangeInfo, una vez por par
    st = {b["sym"]: b for b in lab.status()}
    assert {"v4", "v5"} <= set(st["AAAUSDT"]["arms"])
    var = evidence.by_variant([r | {"model": evidence.FILL_MODEL} for r in rows])
    assert set(var) >= {"control", "v4", "v5"} and all(v["n"] > 0 for v in var.values())
    old = [{"ts": 1, "sym": "A", "pnl": 1.0, "lab": True, "strategy": "v4"}]
    assert evidence.by_variant(old, now=1 + 25 * 3600) == {}                  # fuera de la ventana de 24 h


def test_mejora_de_un_tick_queda_primera_sin_perseguirse_ni_vender_a_perdida():
    from nexus.live_paper_maker import Book
    b = Book("X", notional=10, improve_ticks=1, price_dp=4)
    b.on_book(1.0000, 500, 1.0010, 500)                        # 10 ticks de spread: 1 tick arriba, nadie adelante
    assert b.px == 1.0001 and b.queue == 0.0
    b.on_book(1.0001, 15, 1.0010, 500)                         # en real el mejor bid somos nosotros: no se mueve
    assert b.px == 1.0001 and b.requotes == 1
    b.on_book(0.9995, 500, 1.0010, 500)                        # el bid baja: no paga de más, baja con él
    assert b.px == 0.9996
    b.on_book(1.0007, 500, 1.0010, 500)                        # 3 ticks: todavía entra mejorando
    assert b.px == 1.0008
    b.on_book(1.0008, 500, 1.0010, 500)                        # 2 ticks: sin lugar para ganar, va al final
    assert b.px == 1.0008                                      # (igual al bid: se queda)
    b.on_book(1.0000, 500, 1.0010, 500)
    b.on_trade(1.0000, 1.0, True)                              # se operó en el bid real: nos atraviesa
    assert b.state == "long" and b.entry == 1.0001 and b.px == 1.0009 and b.queue == 0.0
    b.on_book(1.0000, 500, 1.0003, 500)                        # el ask baja: un tick abajo, nunca bajo el piso
    assert b.px == 1.0002
    b.on_book(1.0000, 500, 1.0001, 500)                        # ask en la entrada: el piso manda
    assert b.px == 1.0001
    sin = Book("Y", notional=10, improve_ticks=1)              # sin decimales del par: no entra
    sin.on_book(1.0, 500, 1.001, 500)
    assert sin.px is None
    vig = Book("Z", notional=10)                               # la vigente no cambia: al bid, detrás de la cola
    vig.on_book(1.0000, 500, 1.0010, 500)
    assert vig.px == 1.0 and vig.queue == 500
