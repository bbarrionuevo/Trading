"""Arbitraje estadístico en paper: detecta pares que vuelven, no inventa pares, no mira el futuro."""
import math
import random

from nexus import research_pairs as rp


def _series(n=1500, seed=1, phi=0.9, noise=0.01):
    """B camina al azar; A = B + diferencia que vuelve a la media (AR(1)); C camina sola."""
    rnd = random.Random(seed)
    lb, s, lc = [math.log(100)], [0.0], [math.log(50)]
    for _ in range(n - 1):
        lb.append(lb[-1] + rnd.gauss(0, 0.004))
        s.append(phi * s[-1] + rnd.gauss(0, noise))
        lc.append(lc[-1] + rnd.gauss(0, 0.004))
    a = [math.exp(x + y) for x, y in zip(lb, s)]
    return {"AAA": a, "BBB": [math.exp(x) for x in lb], "CCC": [math.exp(x) for x in lc]}


def test_encuentra_el_par_que_vuelve_y_descarta_el_azar():
    closes = _series()
    pairs = rp.form(closes, 0, 1344)
    assert [(p["a"], p["b"]) for p in pairs] == [("AAA", "BBB")]
    p = pairs[0]
    assert 0.8 < p["beta"] < 1.2 and rp.HL_MIN <= p["hl"] <= rp.HL_MAX and p["adf"] < rp.ADF_CRIT


def test_sin_relacion_no_hay_pares():
    rnd = random.Random(7)
    walk = lambda: [math.exp(sum(rnd.gauss(0, 0.004) for _ in range(i))) for i in range(1, 400)]
    assert rp.form({"XXX": walk(), "YYY": walk()}, 0, 399) == []


def test_ejecuta_en_la_apertura_siguiente_y_cobra_comision():
    p = {"a": "AAA", "b": "BBB", "alpha": 0.0, "beta": 1.0, "mu": 0.0, "sd": 0.01, "hl": 5}
    # z: 0, −3 (abre), −1, +0,5 (vuelve: cierra). Aperturas distintas de los cierres: debe usar las aperturas.
    ca, cb = [100, 97.04, 99, 100.5, 100], [100] * 5
    oa, ob = [100, 99, 98, 99.5, 101], [100] * 5
    tr = rp.simulate({"AAA": (oa, ca), "BBB": (ob, cb)}, [p], 0, 5)
    assert len(tr) == 1 and tr[0]["side"] == 1 and tr[0]["why"] == "media"
    gross = (oa[4] / oa[2] - 1)                              # entra en la apertura de la vela 2, sale en la 4
    assert abs(tr[0]["contado"] - (gross - 2 * rp.COST_SPOT) * 100) < 1e-3
    assert abs(tr[0]["neutral"] - (gross / 2 - 2 * rp.COST_PERP) * 100) < 1e-3


def test_backtest_fuera_de_muestra_gana_con_diferencia_que_vuelve():
    closes = _series(n=rp.FORM_DAYS * 96 + rp.TRADE_DAYS * 96 * 2, seed=3)
    data = {s: (c[:1] + c[:-1], c) for s, c in closes.items()}   # apertura = cierre anterior
    windows = rp.walk_forward(data, len(closes["AAA"]))
    s = rp.summarize(windows, len(windows) * rp.TRADE_DAYS)
    assert len(windows) == 2 and s["neutral"]["n"] >= 3 and s["neutral"]["mean"] > 0
    md = rp.report_backtest(s, windows, 6, ["AAAUSDT", "BBBUSDT"])
    assert "neutral" in md and "contado" in md


def test_paper_en_vivo_compra_al_ask_vende_al_bid_y_cierra_al_cambiar_pares():
    p = {"a": "AAA", "b": "BBB", "alpha": 0.0, "beta": 1.0, "mu": 0.0, "sd": 0.01, "hl": 5}
    st = {"pairs": [p]}
    books = {"AAA": (96.9, 97.0), "BBB": (99.9, 100.0)}
    rp.live_step(st, {"AAA": 97.0, "BBB": 100.0}, books, 1000)          # z ≈ −3: compra A, corto B
    pos = st["open"]["AAA/BBB"]
    assert pos["side"] == 1 and pos["pa"] == 97.0 and pos["pb"] == 99.9
    rp.live_step(st, {"AAA": 98.0, "BBB": 100.0}, books, 1900)          # z ≈ −2: sigue
    assert "AAA/BBB" in st["open"]
    books = {"AAA": (100.0, 100.1), "BBB": (99.9, 100.0)}
    rp.live_step(st, {"AAA": 100.05, "BBB": 100.0}, books, 2800)        # volvió: cierra
    t = st["closed"][-1]
    assert t["why"] == "media" and not st["open"]
    assert abs(t["contado"] - ((100.0 / 97.0 - 1) - 2 * rp.FEE_SPOT) * 100) < 1e-3
    rp.live_step(st, {"AAA": 97.0, "BBB": 100.0}, {"AAA": (96.9, 97.0), "BBB": (99.9, 100.0)}, 3700)
    rp.live_step(st, {"AAA": 97.0, "BBB": 100.0}, {"AAA": (96.9, 97.0), "BBB": (99.9, 100.0)}, 4600, close_all=True)
    assert not st["open"] and st["closed"][-1]["why"] == "fin de ventana"
    assert "AAA/BBB" in rp.report_live(st)


def test_run_live_forma_pares_y_guarda(tmp_path, monkeypatch):
    closes = _series(n=rp.FORM_DAYS * 96)
    data = {s + "USDT": (c, c) for s, c in closes.items()}
    monkeypatch.setattr(rp, "universe", lambda: list(data))
    monkeypatch.setattr(rp, "history", lambda syms, days: (data, list(range(len(closes["AAA"])))))
    monkeypatch.setattr(rp, "binance_klines", lambda s, i, n: [(0, 0, 0, 0, data[s][1][-1], 0)] * 2)
    monkeypatch.setattr(rp, "books_now", lambda syms: {s: (data[s][1][-1] * 0.999, data[s][1][-1]) for s in syms})
    clock = iter(range(10 ** 9, 10 ** 9 + 10 ** 6, 900))
    st = rp.run_live(tmp_path, 30, now=lambda: next(clock), sleep=lambda s: None)
    assert [(p["a"], p["b"]) for p in st["pairs"]] == [("AAAUSDT", "BBBUSDT")]
    assert (tmp_path / "paper_pairs.json").exists() and (tmp_path / "paper_pairs.md").exists()
