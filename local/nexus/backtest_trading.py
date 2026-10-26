"""Backtester de estrategias de trading sobre velas reales de Binance, con comisiones.

Optimiza parámetros en el 70% inicial (in-sample) y evalúa SOLO en el 30% final (out-of-sample).
Una estrategia vale si gana fuera de muestra y le gana a comprar y mantener.
python -m nexus.backtest_trading OUT.md
"""
import json
import sys
from itertools import product

from .discovery.sources import binance_klines

FEE = 0.075 / 100  # por lado (Binance con BNB)


def _sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _rsi(x, n):
    out, g, l = [None] * len(x), 0.0, 0.0
    for i in range(1, len(x)):
        d = x[i] - x[i - 1]
        up, dn = max(d, 0), max(-d, 0)
        if i <= n:
            g += up / n
            l += dn / n
        else:
            g = (g * (n - 1) + up) / n
            l = (l * (n - 1) + dn) / n
        if i >= n:
            out[i] = 100 - 100 / (1 + g / l) if l else 100
    return out


def signals(name, c, h, lo, p):
    """Devuelve posición deseada (0/1) por vela; la decisión de la vela i se ejecuta en i+1."""
    n = len(c)
    if name == "sma_cross":
        f, s = _sma(c, p[0]), _sma(c, p[1])
        return [1 if f[i] and s[i] and f[i] > s[i] else 0 for i in range(n)]
    if name == "rsi_revert":
        r, pos, out = _rsi(c, p[0]), 0, []
        for i in range(n):
            if r[i] is not None:
                pos = 1 if r[i] < p[1] else (0 if r[i] > p[2] else pos)
            out.append(pos)
        return out
    if name == "breakout":
        k, pos, out = p[0], 0, []
        for i in range(n):
            if i >= k:
                hi, low = max(h[i - k:i]), min(lo[i - k:i])
                pos = 1 if c[i] > hi else (0 if c[i] < low else pos)
            out.append(pos)
        return out
    raise ValueError(name)


GRID = {
    "sma_cross": [(f, s) for f, s in product((5, 10, 20, 50), (50, 100, 200)) if f < s],
    "rsi_revert": list(product((7, 14), (20, 25, 30), (55, 65, 75))),
    "breakout": [(k,) for k in (10, 20, 50, 100)],
}


def run(c, pos):
    eq, trades = 1.0, 0
    for i in range(1, len(c)):
        prev = pos[i - 2] if i >= 2 else 0
        cur = pos[i - 1]
        if cur != prev:
            eq *= 1 - FEE
            trades += 1
        if cur:
            eq *= c[i] / c[i - 1]
    return (eq - 1) * 100, trades


def evaluate(k, folds=1):
    """Walk-forward: en cada tramo se optimiza con todo lo anterior y se prueba en el tramo siguiente."""
    c, h, lo = [x[4] for x in k], [x[2] for x in k], [x[3] for x in k]
    n = len(c)
    bounds = [int(n * (0.7 + 0.3 * f / folds)) for f in range(folds + 1)]
    res = []
    for name, grid in GRID.items():
        eq, trades, wins, params = 1.0, 0, 0, []
        for a, b in zip(bounds, bounds[1:]):
            best = max(grid, key=lambda p: run(c[:a], signals(name, c[:a], h[:a], lo[:a], p))[0])
            sig = signals(name, c[:b], h[:b], lo[:b], best)   # solo datos hasta b: sin mirar el futuro
            r, t = run(c[a - 1:b], sig[a - 1:b])
            eq *= 1 + r / 100
            trades += t
            wins += r > (c[b - 1] / c[a - 1] - 1) * 100
            params.append(best)
        res.append({"strategy": name, "params": params[-1], "oos_pct": round((eq - 1) * 100, 2),
                    "trades": trades, "folds_won": f"{wins}/{folds}"})
    bh = (c[-1] / c[bounds[0] - 1] - 1) * 100
    return res, round(bh, 2)


def history(sym, iv, total):
    out, end = [], None
    while len(out) < total:
        k = binance_klines(sym, iv, 1000, end)
        if not k:
            break
        out = k + out
        end = k[0][0] - 1
        if len(k) < 1000:
            break
    return out[-total:]


def main(out="data/trading_backtest.md"):
    lines = ["# Backtest trading (fuera de muestra, comisión 0.075%/lado)\n",
             "| símbolo | intervalo | estrategia | params | OOS % | trades | tramos ganados | buy&hold % | ¿robusta? |",
             "|---|---|---|---|---|---|---|---|---|"]
    raw = []
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"):
        for iv in ("15m", "1h", "4h"):
            try:
                k = history(sym, iv, 5000)
            except Exception as e:
                lines.append(f"| {sym} | {iv} | error: {str(e)[:60]} | | | | | |")
                continue
            res, bh = evaluate(k, folds=5)
            for r in res:
                # robusta: le gana a buy&hold, gana en >=4/5 tramos y tiene >=20 operaciones
                win = r["oos_pct"] > max(bh, 0) and int(r["folds_won"][0]) >= 4 and r["trades"] >= 20
                lines.append(f"| {sym} | {iv} | {r['strategy']} | {r['params']} | {r['oos_pct']} | "
                             f"{r['trades']} | {r['folds_won']} | {bh} | {'SÍ' if win else 'no'} |")
                raw.append({"sym": sym, "iv": iv, "bh": bh, **r})
    open(out, "w").write("\n".join(lines) + "\n")
    open(out.replace(".md", ".json"), "w").write(json.dumps(raw))
    print("\n".join(lines))


if __name__ == "__main__":
    main(*sys.argv[1:2])
