"""Deslistados de Binance (deslistados.json de estudios/deslistados_binance.py): corto en el perpetuo después del
aviso, con stop fijo, salida por tiempo, funding real y 0,2 % de costos. Uso: python estudios/deslistados.py <json>"""
import json
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
E = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
COST = 0.002


def bars_after(ev, t_in, t_out):
    m1 = [b for b in ev["m1"] if t_in <= b[0] < t_out]
    end = ev["m1"][-1][0] + MIN
    return [(b[0], b[2], b[3], b[4]) for b in m1 + [b for b in ev["h1"] if end <= b[0] < t_out]]


def trade(ev, delay_min, stop, hold_h):
    m0 = ev["t0"] // MIN * MIN
    bars = {b[0]: b for b in ev["m1"]}
    pre, entry_bar = bars.get(m0 - MIN), bars.get(m0 + delay_min * MIN)
    if not pre or not entry_bar:
        return None
    entry, t_in = entry_bar[4], entry_bar[0] + MIN
    t_out = t_in + hold_h * HOUR
    path = bars_after(ev, t_in, t_out)
    if not path or path[-1][0] < t_out - 2 * HOUR:
        return None
    out, t_end = path[-1][3], t_out
    for t, h, _lo, _c in path:
        if h >= entry * (1 + stop):
            out, t_end = entry * (1 + stop) * 1.002, t + MIN
            break
    fund = sum(r for ft, r in ev["funding"] if t_in < ft <= t_end)
    return -(out / entry - 1) + fund - COST, entry / pre[4] - 1, 1970 + int(ev["t0"] / DAY / 365.25)


def summary(res):
    if len(res) < 8:
        return f"n {len(res)}"
    xs = [r[0] for r in res]
    return (f"n {len(xs):3d} mediana {statistics.median(xs) * 100:+5.1f}% media {statistics.mean(xs) * 100:+5.1f}% "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% peor {min(xs) * 100:+4.0f}%")


print("monedas:", len(E))
first = [trade(e, 0, 9, 1) for e in E]
moves = sorted(r[1] for r in first if r)
print("caída en el minuto del aviso (mediana):", f"{statistics.median(moves) * 100:+.1f}%" if moves else "-")
for delay in (0, 1, 5, 15, 60):
    for hold in (1, 4, 24, 72, 168):
        res = [r for e in E if (r := trade(e, delay, 0.20, hold))]
        print(f"corto al min {delay:2d}, {hold:3d} h, stop 20 %: {summary(res)}")
print("--- al min 1, 24 h: según el stop y el año ---")
for stop in (0.10, 0.30, 0.50):
    print(f"stop {stop * 100:.0f} %: {summary([r for e in E if (r := trade(e, 1, stop, 24))])}")
res = [r for e in E if (r := trade(e, 1, 0.20, 24))]
for y in range(2022, 2027):
    print(f"año {y}: {summary([r for r in res if r[2] == y])}")
