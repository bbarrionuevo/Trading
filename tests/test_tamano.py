"""Tamaño por estrategia (decision/policy.py): lo fija el riesgo al stop (3 % del saldo) y lo acotan el sobre del dueño
y lo que respalda el estudio de cada estrategia (`ExitProfile.max_x`). El apalancamiento es consecuencia de un stop
cercano, nunca un objetivo (docs/ESTUDIO-APALANCAMIENTO.md)."""
import copy

import pytest

from tradingagent import limits as limits_mod
from tradingagent.decision import earnings
from tradingagent.decision.policy import PolicyConfig, assess, plan
from tradingagent.intelligence.classifier import classify

T0 = 1_793_800_000.0


def kw(lim, **over):
    base = dict(equity=1000.0, cash=1000.0, gross=0.0, net=0.0, n_positions=0, price=400.0, sigma_1m=0.001,
                adv=5e8, fee_taker=0.0005, spread=0.0002, kind="perp", max_leverage=10.0, min_notional=5.0,
                limits=lim, cfg=PolicyConfig())
    return {**base, **over}


def wide():
    """Un sobre amplio, para ver qué topes pone cada estrategia por su cuenta."""
    lim = copy.deepcopy(limits_mod.load())
    lim["per_trade"].update(max_position_pct=100, max_leverage=2)
    lim["portfolio"].update(max_gross_exposure_pct=200, max_net_exposure_pct=100)
    return lim


def test_resultados_arriesgan_el_3_por_ciento_al_stop_de_5():
    sig, a = earnings.signal("TSLA", 1), earnings.assessment("TSLA", 1, 125.0)
    p, why = plan(a, sig, "TSLAUSDT", **kw(wide()))
    assert why == [] and p.sl_frac == 0.05
    assert float(p.notional) == pytest.approx(1000 * 0.03 / (0.05 + p.cost), rel=0.01)   # ~0,58 veces el saldo
    assert p.risk_usd <= 30 + 1e-6
    repo, _ = plan(a, sig, "TSLAUSDT", **kw(limits_mod.load()))                         # el sobre del repo
    assert float(repo.notional) == pytest.approx(float(p.notional))
    strict = copy.deepcopy(limits_mod.load())
    strict["portfolio"]["max_net_exposure_pct"] = 30                                     # un sobre más estricto manda
    capped, _ = plan(a, sig, "TSLAUSDT", **kw(strict))
    assert float(capped.notional) == pytest.approx(300)


def test_sin_estudio_propio_el_tope_es_0_3_del_saldo_aunque_el_sobre_deje_mas():
    title = "Binance hacked: $40M in BTC stolen"
    sig = classify(title)
    ev = {"title": title, "body": "", "tier": 1, "published_at": T0, "received_at": T0, "cluster_sources": 1,
          "assets_hint": [], "source": "binance.anuncios", "id": "h1", "cluster": "h1"}
    a = assess(ev, sig, "BTC", T0 + 5, PolicyConfig(), allows_short=True)
    p, _ = plan(a, sig, "BTCUSDT", **kw(wide(), price=100_000.0))
    assert p is not None and float(p.notional) <= 300 + 1e-6
