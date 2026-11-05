"""Lanzamientos de perpetuos de cripto en Binance (desde 2023): ¿ir corto después del salto inicial? Simulado con
velas de 1 min (primeras ~16 h) y de 1 h (hasta 7 días), stop con los máximos de cada vela, funding real y 0,3 % de
costos de ida y vuelta (libro fino al principio)."""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

L = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
COST = 0.003


def bars_between(ev, t_in, t_out):
    m1 = [b for b in ev["m1"] if t_in <= b[0] < t_out]
    end_m1 = ev["m1"][-1][0] + MIN
    h1 = [b for b in ev["h1"] if end_m1 <= b[0] < t_out]
    return [(b[0], b[2], b[3], b[4]) for b in m1 + h1]


def trade(ev, side, delay, stop, hold_h):
    m1 = ev["m1"]
    if len(m1) < delay + 1 or not m1[0][1]:
        return None
    entry, t_in = m1[delay - 1][4], m1[delay - 1][0] + MIN
    t_out = t_in + hold_h * HOUR
    bars = bars_between(ev, t_in, t_out)
    if not bars or bars[-1][0] < t_out - 2 * HOUR:
        return None                                        # no hay datos para todo el tiempo de la operación
    stop_px = entry * (1 + stop * side * -1)               # corto: arriba; largo: abajo
    out, t_end = None, t_out
    for t, h, lo, _c in bars:
        if (side < 0 and h >= stop_px) or (side > 0 and lo <= stop_px):
            out, t_end = stop_px * (1 - 0.002 * side), t + MIN   # deslizamiento en el stop
            break
    out = bars[-1][3] if out is None else out
    fund = sum(r for ft, r in ev["funding"] if t_in < ft <= t_end)
    pnl = side * (out / entry - 1) - side * fund - COST
    return pnl, entry / m1[0][1] - 1, 1970 + int(ev["t0"] / DAY / 365.25)


def summary(res):
    if len(res) < 10:
        return f"n {len(res)}"
    xs = [r[0] for r in res]
    return (f"n {len(xs):3d} mediana {statistics.median(xs) * 100:+5.1f}% media {statistics.mean(xs) * 100:+5.1f}% "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% peor {min(xs) * 100:+4.0f}%")


print("lanzamientos:", len(L), "por año:", dict(sorted(
    defaultdict(int, {y: sum(1 for e in L if 1970 + int(e["t0"] / DAY / 365.25) == y) for y in range(2023, 2027)}).items())))
moves = defaultdict(list)
for e in L:
    p0 = e["m1"][0][1]
    for m in (1, 5, 15, 60, 240, 960):
        if len(e["m1"]) > m and p0:
            moves[m].append(e["m1"][m - 1][4] / p0 - 1)
print("movimiento desde el primer precio (mediana):",
      {f"{m}m": f"{statistics.median(v) * 100:+.1f}%" for m, v in moves.items()})
for side, name in ((-1, "CORTO"), (1, "LARGO")):
    print(f"--- {name}, stop 30 % ---")
    for delay in (1, 5, 15, 60):
        for hold in (4, 24, 72, 168):
            res = [r for e in L if (r := trade(e, side, delay, 0.30, hold))]
            print(f"entra al min {delay:3d}, sale a las {hold:3d} h: {summary(res)}")
print("--- CORTO al min 15, 72 h: según el stop ---")
for stop in (0.10, 0.20, 0.50, 5.0):
    res = [r for e in L if (r := trade(e, -1, 15, stop, 72))]
    print(f"stop {stop * 100:4.0f}%: {summary(res)}")
print("--- CORTO al min 15, stop 30 %, 72 h: según el salto previo y el año ---")
res = [r for e in L if (r := trade(e, -1, 15, 0.30, 72))]
for lo, hi in ((-9, 0), (0, 0.1), (0.1, 0.3), (0.3, 99)):
    print(f"salto {lo * 100:+.0f}% a {hi * 100:+.0f}%: {summary([r for r in res if lo <= r[1] < hi])}")
for y in range(2023, 2027):
    print(f"año {y}: {summary([r for r in res if r[2] == y])}")
