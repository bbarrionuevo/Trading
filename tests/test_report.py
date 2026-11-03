"""Las piezas del informe "Trading Sistemático por Eventos": triple barrera, vector de evento y alfa,
meta-modelo, ¼-Kelly, costos (raíz cuadrada y Almgren-Chriss), microestructura y PSR/DSR."""
import calendar
import copy
import math
import random

import pytest

from tradingagent import limits as limits_mod
from tradingagent.decision import barriers as bar
from tradingagent.decision.policy import PolicyConfig, assess, kelly, liquidation_ok, plan
from tradingagent.execution import costs
from tradingagent.intelligence import meta, vector
from tradingagent.intelligence.classifier import classify
from tradingagent.marketdata.book import OrderBook
from tradingagent.marketdata.liquidity import SpreadStats, divergence, kyle_lambda, kyle_ratio, l3_notional
from tradingagent.research import evaluation

T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))
MIN = 60_000


@pytest.fixture
def lim():
    return copy.deepcopy(limits_mod.load())


def walk_candles(path, start_ms=T0 * 1000):
    """Velas desde una lista de (open, high, low, close)."""
    return {start_ms + i * MIN: k for i, k in enumerate(path)}


# -- triple barrera ---------------------------------------------------------------------------------
def test_sigma_y_barreras_adaptadas_a_la_volatilidad():
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(80):
        closes.append(closes[-1] * math.exp(rng.gauss(0, 0.002)))
    c = {T0 * 1000 + i * MIN: (x, x, x, x) for i, x in enumerate(closes)}
    s = bar.sigma_1m(c, T0 * 1000 + 70 * MIN)
    assert s == pytest.approx(0.002, rel=0.3)
    assert bar.sigma_1m(c, T0 * 1000 + 20 * MIN) is None                    # menos de 30 retornos
    sl, tp = bar.barriers(0.002, 60)                                        # 1,5σ√60 = 2,3 %
    assert sl == pytest.approx(1.5 * 0.002 * math.sqrt(60)) and tp == pytest.approx(2 * sl)
    assert bar.barriers(0.0001, 60) == pytest.approx((0.006, 0.012))        # piso de 0,6 %: b sigue siendo 2
    assert bar.barriers(0.05, 240) == pytest.approx((0.05, 0.10))           # techo del 5 %
    assert bar.prices(100.0, -1, 0.01, 0.02) == pytest.approx((101.0, 98.0))  # corto: stop arriba


def test_primera_barrera_tocada():
    c = walk_candles([(100, 100.5, 99.5, 100), (100, 101, 99.8, 100.8), (100.8, 103, 100.5, 102.9)])
    assert bar.walk(c, side=1, stop=99, tp=102, start_ms=T0 * 1000, end_ms=T0 * 1000 + 2 * MIN) == ("tp", 102, T0 * 1000 + 2 * MIN)
    both = walk_candles([(100, 103, 98, 100)])                              # toca los dos: cuenta el stop
    assert bar.walk(both, side=1, stop=99, tp=102, start_ms=T0 * 1000, end_ms=T0 * 1000)[0] == "sl"
    gap = walk_candles([(100, 100, 99.6, 99.8), (97, 97.5, 96, 97)])        # abre debajo del stop: desde ahí
    assert bar.walk(gap, side=1, stop=99, tp=102, start_ms=T0 * 1000, end_ms=T0 * 1000 + MIN, slip=0.002)[1] == \
        pytest.approx(97 * 0.998)
    touch = walk_candles([(100, 102, 99.5, 101)])                           # el TP se llena solo si lo atraviesa
    assert bar.walk(touch, side=1, stop=99, tp=102, start_ms=T0 * 1000, end_ms=T0 * 1000) is None
    entry = walk_candles([(100, 100.2, 95, 100.1)])                         # minuto de la entrada: solo el cierre
    assert bar.walk(entry, side=1, stop=99, tp=102, start_ms=T0 * 1000, end_ms=T0 * 1000, entry_ms=T0 * 1000 + 30_000) is None
    short = walk_candles([(100, 100.4, 97.5, 98)])
    assert bar.walk(short, side=-1, stop=101, tp=98, start_ms=T0 * 1000, end_ms=T0 * 1000)[0] == "tp"
    exp = walk_candles([(100, 100.1, 99.9, 100)] * 5)
    assert bar.walk(exp, side=1, stop=99, tp=102, start_ms=T0 * 1000, end_ms=T0 * 1000 + 4 * MIN,
                    until_ms=T0 * 1000 + 3 * MIN, slip=0.001) == ("exp", pytest.approx(99.9), T0 * 1000 + 3 * MIN)


# -- vector de evento y alfa ------------------------------------------------------------------------
def ev(title, **kw):
    return {"id": "e", "title": title, "source": "s", "tier": 2, "cluster": "c", "cluster_sources": 1,
            "published_at": T0 - 10, "received_at": T0 - 5, **kw}


def test_vector_c_p_i_m_d_y_alfa():
    s = classify("Coinbase will list PEPE")
    v = vector.vector(s, "PEPE", ev("Coinbase will list PEPE"))
    assert (v.C, v.P, v.I, v.M, v.D) == (10.0, 0.6, 7.0, 8.0, 5.0) and v.alpha == pytest.approx(0.6 * 0.7 * 0.8)
    rumor = classify("Coinbase reportedly plans to list PEPE")
    assert vector.vector(rumor, "PEPE", ev("x")).P == pytest.approx(0.36)   # la certeza escala P
    official = vector.vector(s, "PEPE", ev("Coinbase will list PEPE", tier=1, cluster_sources=3))
    assert official.I == 9.0                                                # +1 oficial, +1 por dos copias
    hack = classify("Exchange hacked, millions drained")                    # sin activo: BTC de referencia
    assert vector.vector(hack, "BTC", ev("Exchange hacked, millions drained")).C == 3.0
    macro = classify("Fed cuts interest rates by 50 basis points")
    assert vector.vector(macro, "BTC", ev("Fed cuts interest rates by 50 basis points")).C == 7.0
    body = classify("Exchange update", "Kraken will delist XRP on Friday.")
    assert vector.vector(body, "XRP", ev("Exchange update", body="Kraken will delist XRP on Friday.")).C == 7.0


def test_decaimiento_y_obsolescencia():
    assert vector.decay(0.4, 120) == pytest.approx(0.2) and vector.decay(0.4, 0) == 0.4
    assert not vector.stale(360) and vector.stale(361)                      # 3 vidas medias
    assert vector.age_s({"published_at": T0 - 100, "received_at": T0 - 50}, T0) == (100, "publicada")
    assert vector.age_s({"published_at": None, "received_at": T0 - 50}, T0) == (170, "llegada+vida_media")
    assert vector.age_s({"published_at": T0 + 999, "received_at": T0 - 50}, T0)[1] == "llegada+vida_media"


def test_assess_corta_por_c_alfa_obsolescencia_y_meta_modelo():
    cfg = PolicyConfig()
    s = classify("Coinbase will list PEPE")
    fresh = assess(ev("Coinbase will list PEPE"), s, "PEPE", T0, cfg)
    assert fresh.reasons == () and fresh.side == 1 and fresh.p > 0.6
    late = assess(ev("Coinbase will list PEPE", published_at=T0 - 90), s, "PEPE", T0, cfg)
    assert late.reasons[0].startswith("meta_p_baja")                         # el alfa ya decayó
    old = assess(ev("Coinbase will list PEPE", published_at=T0 - 400), s, "PEPE", T0, cfg)
    assert old.reasons[0] == "alfa_obsoleta:400s"
    hack = classify("Exchange hacked, millions drained")
    weak = assess(ev("Exchange hacked, millions drained"), hack, "BTC", T0, cfg)
    assert "relevancia_baja:C=3" in weak.reasons
    neg = classify("Binance hacked: $40M in BTC stolen")
    short = assess(ev("Binance hacked: $40M in BTC stolen"), neg, "BTC", T0, cfg)
    assert short.side == -1 and short.reasons == ()
    spot = assess(ev("Binance hacked: $40M in BTC stolen"), neg, "BTC", T0, cfg, allows_short=False)
    assert "corto_no_disponible" in spot.reasons


# -- meta-modelo -------------------------------------------------------------------------------------
def test_meta_modelo_prior_y_aprendizaje_que_solo_reduce():
    assert meta.prior(0.0) == pytest.approx(1 / 3) and meta.prior(0.25) == pytest.approx(0.60)
    assert meta.prior(-0.25) == meta.prior(0.25) and meta.prior(0.5) > 0.75
    rng = random.Random(3)
    honest = [(p, int(rng.random() < p)) for p in (meta.prior(rng.uniform(0, 0.5)) for _ in range(3000))]
    assert meta.fit_kappa(honest) == pytest.approx(1.0, abs=0.1)            # calibrado: queda como está
    flat = [(p, int(rng.random() < 1 / 3)) for p, _ in honest]               # sin ventaja real
    k = meta.fit_kappa(flat)
    assert k < 0.2
    lucky = [(p, 1) for p, _ in honest[:100]]                                # mejor que el prior: no sube
    assert meta.fit_kappa(lucky) == 1.0
    fitted = meta.fit({"listado:largo": flat[:40], "hack:corto": honest[:10]})
    m = meta.MetaModel(fitted)
    assert m.kappa("listado", "largo") == pytest.approx(k, abs=0.15)
    assert m.kappa("hack", "corto") == fitted["global"]["kappa"]            # n < 30: usa el global
    assert m.p(0.4, "listado", "largo") < meta.prior(0.4)
    assert meta.MetaModel().p(0.4, "x", "largo") == meta.prior(0.4)


# -- tamaño ------------------------------------------------------------------------------------------
def test_cuarto_de_kelly():
    assert kelly(0.6, 2.0) == pytest.approx(0.25 * (0.6 * 2 - 0.4) / 2)
    assert kelly(0.3, 2.0) == 0.0 and kelly(0.9, 0.0) == 0.0


def test_plan_triple_barrera_costos_kelly_y_topes(lim):
    cfg = PolicyConfig()
    s = classify("Coinbase will list PEPE")
    a = assess(ev("Coinbase will list PEPE"), s, "PEPE", T0, cfg)
    kw = dict(equity=1000.0, cash=1000.0, gross=0.0, net=0.0, n_positions=0, price=1.0, sigma_1m=0.002,
              adv=5e7, fee_taker=0.001, spread=0.0005, kind="spot", max_leverage=1.0, min_notional=5.0,
              limits=lim, cfg=cfg)
    p, why = plan(a, s, "PEPEUSDT", **kw)
    assert why == [] and p.side == 1 and p.tp_frac == pytest.approx(2 * p.sl_frac)
    assert p.sl_frac == pytest.approx(1.5 * 0.002 * math.sqrt(60))
    assert p.b_net < 2 and p.cost > 2 * 0.001                               # comisiones, spread e impacto
    risk = p.sl_frac + p.cost                                               # Kelly dice cuánto ARRIESGAR:
    assert float(p.notional) == pytest.approx(min(1000 * p.kelly / risk, 10 / risk, 400, 300), abs=0.01)
    assert float(p.notional) == pytest.approx(300.0)                        # acá manda la neta (30 %)
    assert p.risk_usd <= 1000 * lim["per_trade"]["max_risk_pct"] / 100 + 1e-9
    assert p.slices == 1                                                    # Almgren-Chriss: un solo tramo
    capped, _ = plan(a, s, "PEPEUSDT", **{**kw, "net": 250.0})              # exposición neta ≤ 30 %
    assert float(capped.notional) == pytest.approx(50.0)
    shorted = assess(ev("Binance hacked: $40M in BTC stolen"), classify("Binance hacked: $40M in BTC stolen"),
                     "BTC", T0, cfg)
    p2, _ = plan(shorted, classify("Binance hacked: $40M in BTC stolen"), "BTCUSDT",
                 **{**kw, "net": 250.0, "kind": "perp"})                     # un corto achica la neta: entra entero
    assert p2.side == -1 and float(p2.notional) > 50
    no_edge, why = plan(a, s, "PEPEUSDT", **{**kw, "fee_taker": 0.02})       # b neto > 0 pero sin ventaja
    assert no_edge is None and why[0].startswith("sin_ventaja_neta")
    expensive, why = plan(a, s, "PEPEUSDT", **{**kw, "fee_taker": 0.03})     # el costo supera al take-profit
    assert expensive is None and why[0].startswith("costos_mayores_que_el_objetivo")
    assert plan(a, s, "PEPEUSDT", **{**kw, "n_positions": 3})[1] == ["maximo_de_posiciones"]
    assert plan(a, s, "PEPEUSDT", **{**kw, "sigma_1m": None})[1] == ["sin_volatilidad_medible"]
    assert plan(a, s, "PEPEUSDT", **{**kw, "cash": 3.0})[1][0].startswith("nominal_menor_al_minimo")
    risky, _ = plan(a, s, "PEPEUSDT", **{**kw, "sigma_1m": 0.02})            # mucha volatilidad: stop lejos
    assert risky.risk_usd <= 10.0 + 1e-9 and risky.sl_frac == 0.05


def test_liquidacion_lejos_del_stop():
    cfg = PolicyConfig()
    assert liquidation_ok(1.0, 1, 0.05, 1.0, 0.004, cfg)                    # 1x: liquidación a ~100 %
    assert not liquidation_ok(1.0, 1, 0.05, 10.0, 0.004, cfg)               # 10x: 9,6 % < 3 × 5 %


# -- costos ------------------------------------------------------------------------------------------
def test_raiz_cuadrada_y_almgren_chriss():
    assert costs.sqrt_impact(1e6, 1e8, 0.04) == pytest.approx(0.6 * 0.04 * 0.1)
    assert costs.sqrt_impact(10, None, 0.04) == 0.01 and costs.sqrt_impact(0, 1e8, 0.04) == 0.0
    assert costs.round_trip(0.001, 0.0005, 0.0001) == pytest.approx(0.0027)
    twap = costs.almgren_chriss(100, 10, 10, sigma=0.5, eta=0.01)
    assert twap["ordenes"] == pytest.approx([10] * 10)                       # sin aversión al riesgo: lineal
    ac = costs.almgren_chriss(100, 10, 10, sigma=0.5, eta=0.01, lam=0.01)
    assert ac["ordenes"][0] > ac["ordenes"][-1] and sum(ac["ordenes"]) == pytest.approx(100)  # más rápido al principio
    assert ac["varianza"] < twap["varianza"] and ac["costo_esperado"] > twap["costo_esperado"]
    patient = costs.best_schedule(0.0005, 100_000, 0.0005, impact=1e-5)     # sin apuro: repartir ahorra impacto
    assert patient["tramos"] > 1
    urgent = costs.best_schedule(0.0005, 100_000, 0.0005, impact=1e-5, drift=350.0)   # noticia: el alfa se va
    assert urgent["tramos"] == 1
    drift = costs.almgren_chriss(100, 10, 10, sigma=0.5, eta=0.01, drift=1.0)
    assert drift["costo_esperado"] == pytest.approx(twap["costo_esperado"] + 1.0 * 1.0 * sum(10 * (j + 0.5) for j in range(10)))
    with pytest.raises(ValueError):
        costs.almgren_chriss(0, 10, 1, sigma=1, eta=1)


# -- microestructura ---------------------------------------------------------------------------------
def test_spread_z_l3_divergencia_y_kyle():
    st = SpreadStats()
    for i in range(30):
        st.update(2.0 + 0.2 * (i % 2), T0 + i)
    assert st.z(2.1) == pytest.approx(0.0, abs=0.3) and st.z(4.0) > 2.5
    assert SpreadStats().z(10.0) is None                                    # sin historia: no se juzga
    assert SpreadStats.from_dict(st.as_dict()).n == 30
    b = OrderBook.from_levels("t", "X", [("99", "1"), ("98", "1"), ("97", "1"), ("96", "50")],
                              [("101", "1"), ("102", "1"), ("103", "1")], T0)
    assert l3_notional(b) == pytest.approx(99 + 98 + 97 + 101 + 102 + 103)
    assert divergence(b, 101.0) == pytest.approx(0.01) and divergence(b, None) is None
    rng = random.Random(5)
    calm = {}
    for i in range(60):
        q = rng.uniform(-1000, 1000)
        r = 1e-6 * q * (1 if i < 50 else 6) + rng.gauss(0, 1e-5)             # los últimos 10 min, 6 veces más λ
        calm[T0 * 1000 + i * MIN] = (100.0, 100.0, 100.0, 100.0 * math.exp(r), 5000.0, (5000.0 + q) / 2)
    assert kyle_lambda(calm, T0 * 1000, T0 * 1000 + 50 * MIN) == pytest.approx(1e-6, rel=0.3)
    assert kyle_ratio(calm, T0 * 1000 + 60 * MIN) > 4
    assert kyle_ratio({k: v[:4] for k, v in calm.items()}, T0 * 1000 + 60 * MIN) is None    # sin volumen


# -- evaluación -------------------------------------------------------------------------------------
def test_psr_y_dsr():
    rng = random.Random(11)
    good = [rng.gauss(0.01, 0.02) for _ in range(200)]
    noise = [rng.gauss(0.0, 0.02) for _ in range(200)]
    assert evaluation.dsr(good) > 0.95 and evaluation.verdict(evaluation.dsr(good), 200) == "promover"
    assert evaluation.dsr(noise) < 0.95
    assert evaluation.dsr(good, n_trials=100, var_sr=0.05) < evaluation.dsr(good)   # más pruebas: más exigente
    assert evaluation.expected_max_sharpe(1, 0.1) == 0.0 and evaluation.expected_max_sharpe(10, 0.1) > 0
    assert evaluation.psr(0.5, 50, skew=-2, kurt=10) < evaluation.psr(0.5, 50)      # colas pesadas: penaliza
    assert evaluation.verdict(0.5, 10) == "en_evaluacion" and evaluation.verdict(0.5, 40) == "descartar"
    trades = [{"tipo": "salida", "pnl_neto": str(x * 10), "notional_entrada": "10", "strategy_tag": "v1"} for x in noise[:40]]
    trades += [{"tipo": "salida", "pnl_neto": str(x * 10), "notional_entrada": "10", "strategy_tag": "v2"} for x in good[:40]]
    by = evaluation.by_version(trades)
    assert set(by) == {"v1", "v2"} and by["v2"]["dsr"] > by["v1"]["dsr"] and by["v1"]["n"] == 40


def test_etiqueta_de_triple_barrera_desde_la_llegada():
    from tradingagent.research.labeler import reaction, triple_barrier

    m0 = T0 * 1000
    path = {}
    for i in range(-70, 80):                                                # quieto antes; +2 % a los 5 min
        m = m0 + i * MIN
        price = 100.0 * (1.02 if i >= 5 else 1.0) * (1 + 0.0002 * (-1) ** i)   # algo de ruido para σ
        path[m] = (price, price * 1.0005, price * 0.9995, price, 1000.0, 500.0)
    tb = triple_barrier(path, m0, m0 + 2 * MIN)                             # llegó a los 2 min: entra a los 3
    assert tb["sigma_1m"] > 0 and tb["tb_sl"] == 0.006 and tb["tb_tp"] == 0.012
    assert tb["tb_largo"] == 1 and tb["tb_largo_r"] == pytest.approx(0.012)   # el salto tocó el TP
    assert tb["tb_corto"] == -1 and tb["tb_corto_r"] < -0.006                  # el corto se comió el stop
    late = triple_barrier(path, m0, m0 + 10 * MIN)                          # llegó tarde: ya estaba en el precio
    assert late["tb_largo"] == 0 and late["tb_corto"] == 0                  # expiraron sin tocar nada
    full = reaction(path, T0, T0 + 120)
    assert full["tb_largo"] == 1 and "r_recv_15m" in full
    assert triple_barrier({m0: (1, 1, 1, 1)}, m0, m0) == {"sigma_1m": None}   # sin historia: sin etiqueta
