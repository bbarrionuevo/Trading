"""Tests del control de seguridad del CI (R-02, R-14): que rechace lo que debe rechazar."""
import copy
import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gate", ROOT / "backtest" / "run.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
S = yaml.safe_load((ROOT / "local/config/settings.yaml").read_text(encoding="utf-8"))
A = yaml.safe_load((ROOT / "local/config/agent.yaml").read_text(encoding="utf-8"))


def test_config_actual_pasa():
    assert gate.check(S, A) == []


def test_rechaza_modo_real():
    a = copy.deepcopy(A); a["live"] = True
    s = copy.deepcopy(S); s["paper_trading"] = False
    assert len(gate.check(s, a)) == 2


def test_rechaza_relajar_cada_limite():
    for k, v in gate.BASELINE_MAX.items():
        a = copy.deepcopy(A); a["risk"][k] = v * 2
        assert gate.check(S, a), k
    a = copy.deepcopy(A); a["risk"]["stops_cooldown_min"] = 1
    assert gate.check(S, a)
    a = copy.deepcopy(A); del a["risk"]["max_drawdown_eur"]
    assert gate.check(S, a)


def test_permite_endurecer():
    a = copy.deepcopy(A); a["risk"]["daily_loss_limit_eur"] = 1
    assert gate.check(S, a) == []
