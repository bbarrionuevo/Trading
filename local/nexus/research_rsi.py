"""Estudio profundo de reversión RSI: muchas monedas, historia larga, 10 tramos walk-forward.

Métricas por moneda: retorno OOS compuesto, tramos positivos, tramos que le ganan a buy&hold,
drawdown máximo y % de tiempo invertido. python -m nexus.research_rsi OUT.md [interval] [velas]
"""
import json
import sys

from .backtest_trading import GRID, FEE, history, signals

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
        "LINKUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT", "BCHUSDT", "NEARUSDT", "ATOMUSDT", "UNIUSDT",
        "XLMUSDT", "ETCUSDT", "FILUSDT", "APTUSDT"]


def run_curve(c, pos):
    eq, peak, mdd, trades, inmkt = 1.0, 1.0, 0.0, 0, 0
    for i in range(1, len(c)):
        prev = pos[i - 2] if i >= 2 else 0
        if pos[i - 1] != prev:
            eq *= 1 - FEE
            trades += 1
        if pos[i - 1]:
            eq *= c[i] / c[i - 1]
            inmkt += 1
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return eq, mdd, trades, inmkt


def study(k, folds=10, name="rsi_revert"):
    c, h, lo = [x[4] for x in k], [x[2] for x in k], [x[3] for x in k]
    n = len(c)
    b = [int(n * (0.4 + 0.6 * f / folds)) for f in range(folds + 1)]
    grid = GRID[name]
    eq, pos_f, beat, mdd, trades, inm, tot = 1.0, 0, 0, 0.0, 0, 0, 0
    for a, z in zip(b, b[1:]):
        lb = max(0, a - (z - a) * 6)               # optimiza en una ventana móvil previa
        best = max(grid, key=lambda p: run_curve(c[lb:a], signals(name, c[lb:a], h[lb:a], lo[lb:a], p))[0])
        sig = signals(name, c[:z], h[:z], lo[:z], best)
        e, dd, t, im = run_curve(c[a - 1:z], sig[a - 1:z])
        eq *= e
        pos_f += e > 1
        beat += e > c[z - 1] / c[a - 1]
        mdd, trades, inm, tot = max(mdd, dd), trades + t, inm + im, tot + (z - a)
    bh = c[-1] / c[b[0] - 1]
    return {"oos_pct": round((eq - 1) * 100, 2), "bh_pct": round((bh - 1) * 100, 2),
            "folds_pos": pos_f, "folds_beat_bh": beat, "max_dd_pct": round(mdd * 100, 1),
            "trades": trades, "time_in_mkt_pct": round(inm / tot * 100, 1)}


def main(out="data/research_rsi.md", interval="1h", candles=15000):
    rows = []
    for s in SYMS:
        try:
            rows.append((s, study(history(s, interval, int(candles)))))
        except Exception as e:
            rows.append((s, {"error": str(e)[:80]}))
    ok = [r for _, r in rows if "oos_pct" in r]
    agg = {
        "monedas": len(ok),
        "oos_medio_pct": round(sum(r["oos_pct"] for r in ok) / len(ok), 2) if ok else None,
        "bh_medio_pct": round(sum(r["bh_pct"] for r in ok) / len(ok), 2) if ok else None,
        "monedas_oos_positivo": sum(r["oos_pct"] > 0 for r in ok),
        "monedas_le_ganan_bh": sum(r["oos_pct"] > r["bh_pct"] for r in ok),
    }
    L = [f"# Estudio RSI reversión — {interval}, {candles} velas, 10 tramos walk-forward\n",
         f"**Agregado:** {agg}\n",
         "| símbolo | OOS % | B&H % | tramos + | tramos > B&H | MaxDD % | trades | % tiempo invertido |",
         "|---|---|---|---|---|---|---|---|"]
    for s, r in rows:
        if "error" in r:
            L.append(f"| {s} | error {r['error']} |||||||")
        else:
            L.append(f"| {s} | {r['oos_pct']} | {r['bh_pct']} | {r['folds_pos']}/10 | {r['folds_beat_bh']}/10 | "
                     f"{r['max_dd_pct']} | {r['trades']} | {r['time_in_mkt_pct']} |")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(*sys.argv[1:4])
