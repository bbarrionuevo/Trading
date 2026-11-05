"""Registro fiscal (research/fiscal.py): resultado y volumen por mes de Argentina, desde las operaciones."""
import json
from datetime import datetime, timedelta, timezone

from tradingagent.research.fiscal import ledger, render, to_csv

AR = timezone(timedelta(hours=-3))


def ts(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=AR).timestamp()


def test_resultado_y_volumen_por_mes_de_argentina(tmp_path):
    rows = [{"tipo": "entrada", "ts": ts(2026, 11, 5), "notional": "55.2", "fee": "0.022"},
            {"tipo": "salida", "ts": ts(2026, 11, 5), "qty": "7.24", "price": "7.59", "fee": "0.022",
             "pnl_neto": "-0.27"},
            {"tipo": "entrada", "ts": ts(2026, 11, 30, 22), "notional": "10", "fee": "0.004"},   # 30-nov 22 h AR
            {"tipo": "salida", "ts": ts(2026, 11, 30, 23), "qty": "1", "price": "11", "fee": "0.004",
             "pnl_neto": "0.99"}]
    f = tmp_path / "operaciones" / "2026-12.jsonl"                    # en UTC ya es diciembre: cuenta noviembre
    f.parent.mkdir(parents=True)
    f.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    led = ledger(tmp_path, 2026)
    assert list(led) == ["2026-11"]
    m = led["2026-11"]
    assert m["operaciones"] == 2 and m["ganadas"] == 1
    assert abs(float(m["volumen"]) - (55.2 + 7.24 * 7.59 + 10 + 11)) < 1e-9
    assert abs(float(m["resultado"]) - 0.72) < 1e-9 and abs(float(m["comisiones"]) - 0.052) < 1e-9
    assert "Total" in render(led, "real")
    to_csv(led, tmp_path / "fiscal.csv")
    assert (tmp_path / "fiscal.csv").read_text(encoding="utf-8").startswith("mes,operaciones")
    assert ledger(tmp_path, 2025) == {}
