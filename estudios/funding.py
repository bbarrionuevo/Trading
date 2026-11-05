"""¿El funding extremo anticipa el precio? Perpetuos de cripto de Binance (los de tendencia.json: 114 contratos desde
2020, incluidos los cerrados), velas diarias y funding real. Por cada día y contrato líquido (≥ 20 M USDT por día), el
funding del día (suma de los cobros) y lo que pasa los días siguientes: rendimiento de un CORTO (o un largo) con el
funding cobrado o pagado y 0,1 % de costos de ida y vuelta. Uso: python estudios/funding.py <carpeta>/tendencia.json"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
DATA = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
DAY = 86_400_000
COST = 0.001
T_SPLIT = 1672531200000       # 2023


def series(v):
    bars = {b[0]: b for b in v["velas"]}
    fund = defaultdict(float)
    for t, r in v["funding"]:
        fund[t - t % DAY] += r
    return bars, fund


rows = []                      # (día, funding del día, {k: (rend. del corto, máximo en contra)}, año)
for sym, v in DATA.items():
    bars, fund = series(v)
    days = sorted(bars)
    for i, d in enumerate(days):
        if i < 30 or i + 7 >= len(days) or d not in fund:
            continue
        adv = statistics.mean(bars[x][5] for x in days[i - 29:i + 1])
        if adv < 20e6:
            continue
        c0 = bars[d][4]
        out = {}
        for k in (1, 3, 7):
            fut = days[i + 1:i + 1 + k]
            if len(fut) < k or fut[-1] - d != k * DAY:
                break
            ck = bars[fut[-1]][4]
            got = sum(fund.get(x, 0.0) for x in fut)          # el corto cobra el funding positivo
            worst = max(bars[x][2] for x in fut) / c0 - 1     # lo peor para el corto (el máximo)
            out[k] = (-(ck / c0 - 1) + got - COST, worst)
        if len(out) == 3:
            rows.append((d, fund[d], out, sym))


def line(name, sel, k, side=-1):
    xs = [(r[2][k][0] if side < 0 else -r[2][k][0] - 2 * COST) for r in sel]
    if len(xs) < 20:
        return f"{name}: n {len(xs)}"
    return (f"{name:28s} n {len(xs):5d} mediana {statistics.median(xs) * 100:+5.2f}% media {statistics.mean(xs) * 100:+5.2f}%"
            f" positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}%")


print("días-contrato:", len(rows))
buckets = [(">= +0,30 %/día", 0.003, 9), ("+0,10 a +0,30 %", 0.001, 0.003), ("+0,03 a +0,10 %", 0.0003, 0.001),
           ("0 a +0,03 %", 0.0, 0.0003), ("-0,10 a 0 %", -0.001, 0.0), ("<= -0,10 %/día", -9, -0.001)]
for k in (1, 3, 7):
    print(f"--- CORTO {k} día(s), según el funding del día ---")
    for name, lo, hi in buckets:
        sel = [r for r in rows if lo <= r[1] < hi]
        a = [r for r in sel if r[0] < T_SPLIT]
        b = [r for r in sel if r[0] >= T_SPLIT]
        print(line(name, sel, k), "|", line("2020-22", a, k).split(" n ")[-1], "|", line("2023+", b, k).split(" n ")[-1])
print("--- el más extremo de cada día (uno por día), corto 3 días si funding >= +0,30 %/día ---")
best = {}
for r in rows:
    if r[1] >= 0.003 and (r[0] not in best or r[1] > best[r[0]][1]):
        best[r[0]] = r
sel = list(best.values())
for y in range(2020, 2027):
    ys = [r for r in sel if 1970 + int(r[0] / DAY / 365.25) == y]
    print(f"{y}:", line("", ys, 3))
print("peor máximo en contra en 3 días (los extremos):",
      f"{max(r[2][3][1] for r in sel) * 100:+.0f}%" if sel else "-")
