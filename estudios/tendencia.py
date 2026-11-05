"""Estrategias sistemáticas en los perpetuos de cripto de Binance (velas diarias, 2020 en adelante), con costos
(0,08 % por unidad operada: comisión de tomador + deslizamiento) y el funding real de cada día.
- TS (tendencia, lo que hacen los fondos CTA): cada activo largo o corto según el signo de su rendimiento de N días,
  pesos inversos a la volatilidad, la cartera escalada a una volatilidad objetivo con tope de apalancamiento.
- XS (entre activos, neutral al mercado): cada semana, largo el tercio que más subió en N días y corto el que menos
  (momentum), o al revés (reversión).
Período de prueba: 2020-2022 (en muestra) y 2023 en adelante (fuera de muestra)."""
import json
import math
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DATA = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
DAY = 86_400_000
COST = 0.0008
MIN_ADV = 20e6
T_IS = 1672531200000          # 1-ene-2023
START = 1577836800000         # 1-ene-2020

# ---- series alineadas por día ---------------------------------------------------------------------------------
days = sorted({b[0] for v in DATA.values() for b in v["velas"] if b[0] >= START - 200 * DAY})
idx = {d: i for i, d in enumerate(days)}
syms = sorted(DATA)
close = {s: [None] * len(days) for s in syms}
qvol = {s: [None] * len(days) for s in syms}
fund = {s: [0.0] * len(days) for s in syms}
for s in syms:
    for t, _o, _h, _lo, c, qv in DATA[s]["velas"]:
        if t in idx:
            close[s][idx[t]], qvol[s][idx[t]] = c, qv
    for ft, rate in DATA[s]["funding"]:
        d = ft - ft % DAY
        if d in idx:
            fund[s][idx[d]] += rate


def ret(s, i):
    a, b = close[s][i - 1], close[s][i]
    return b / a - 1 if a and b else None


R = {s: [None] + [ret(s, i) for i in range(1, len(days))] for s in syms}


def vol(s, i, n=30):
    xs = [R[s][j] for j in range(i - n + 1, i + 1) if R[s][j] is not None]
    return statistics.pstdev(xs) if len(xs) >= n * 0.8 else None


def _active(s, i):
    if i < 130 or close[s][i] is None or close[s][i - 130] is None:
        return False
    q = [qvol[s][j] for j in range(i - 29, i + 1) if qvol[s][j]]
    return len(q) >= 25 and sum(q) / len(q) >= MIN_ADV


ACT = {s: [_active(s, i) for i in range(len(days))] for s in syms}
VOL = {s: [vol(s, i) if ACT[s][i] else None for i in range(len(days))] for s in syms}


def active(s, i):
    return ACT[s][i]


def stats(rets, t0, t1):
    xs = [r for d, r in rets if t0 <= d < t1]
    if len(xs) < 60:
        return None
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in xs:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    yrs = len(xs) / 365
    cagr = eq ** (1 / yrs) - 1 if eq > 0 else -1
    sd = statistics.pstdev(xs) * math.sqrt(365)
    sh = statistics.mean(xs) * 365 / sd if sd else 0
    return cagr, sd, sh, mdd


def yearly(rets):
    out = {}
    for d, r in rets:
        y = 1970 + int(d / DAY / 365.25)
        out[y] = out.get(y, 1.0) * (1 + r)
    return " ".join(f"{y}:{(v - 1) * 100:+.0f}%" for y, v in sorted(out.items()))


def run_ts(n_look, target=0.20, lmax=2.0, rebalance=1, topk=0, long_only=False):
    w_prev, rets, raw_hist, turn, flips = {}, [], [], 0.0, 0
    start = idx.get(START, 0)
    for i in range(max(start, 130), len(days) - 1):
        if (i - start) % rebalance == 0:
            raw, score = {}, {}
            for s in syms:
                if not active(s, i) or close[s][i - n_look] is None:
                    continue
                v = VOL[s][i]
                if not v:
                    continue
                mom = close[s][i] / close[s][i - n_look] - 1
                sig = 1 if mom > 0 else -1
                if long_only and sig < 0:
                    continue
                raw[s] = sig / v
                score[s] = abs(mom) / (v * math.sqrt(n_look))
            if topk:
                raw = {s: raw[s] for s in sorted(score, key=score.get, reverse=True)[:topk]}
            tot = sum(abs(x) for x in raw.values())
            w_raw = {s: x / tot for s, x in raw.items()} if tot else {}
            sd = statistics.pstdev(raw_hist[-60:]) * math.sqrt(365) if len(raw_hist) >= 30 else None
            k = min(lmax, target / sd) if sd else min(lmax, 1.0)
            w = {s: x * k for s, x in w_raw.items()}
            flips += sum(1 for s in w if s not in w_prev or (w[s] > 0) != (w_prev[s] > 0))
        else:
            w = w_prev
        cost = sum(abs(w.get(s, 0) - w_prev.get(s, 0)) for s in set(w) | set(w_prev)) * COST
        turn += sum(abs(w.get(s, 0) - w_prev.get(s, 0)) for s in set(w) | set(w_prev))
        r_raw = sum((x / max(k, 1e-9)) * (R[s][i + 1] or 0) for s, x in w.items()) if w else 0.0
        raw_hist.append(r_raw)
        pnl = sum(x * ((R[s][i + 1] or 0) - fund[s][i + 1]) for s, x in w.items()) - cost
        rets.append((days[i + 1], pnl))
        GROSS.append(sum(abs(x) for x in w.values()))
        w_prev = w
    return rets, turn / max(1, len(rets)) * 365, flips / max(1, len(rets)) * 30


GROSS: list[float] = []


def run_xs(n_look, mode, rebalance=7):
    w_prev, rets, turn = {}, [], 0.0
    start = idx.get(START, 0)
    w = {}
    for i in range(max(start, 130), len(days) - 1):
        if (i - start) % rebalance == 0:
            cand = [(close[s][i] / close[s][i - n_look] - 1, s) for s in syms
                    if active(s, i) and close[s][i - n_look]]
            cand.sort()
            k = len(cand) // 3
            if k >= 3:
                lo, hi = [s for _, s in cand[:k]], [s for _, s in cand[-k:]]
                if mode == "rev":
                    lo, hi = hi, lo
                w = {**{s: 0.5 / k for s in hi}, **{s: -0.5 / k for s in lo}}
        dw = sum(abs(w.get(s, 0) - w_prev.get(s, 0)) for s in set(w) | set(w_prev))
        turn += dw
        pnl = sum(x * ((R[s][i + 1] or 0) - fund[s][i + 1]) for s, x in w.items()) - dw * COST
        rets.append((days[i + 1], pnl))
        w_prev = dict(w)
    return rets, turn / max(1, len(rets)) * 365


def line(name, rets, extra=""):
    a, b = stats(rets, START, T_IS), stats(rets, T_IS, 10**14)
    def f(x):
        return "-" if x is None else f"{x[0] * 100:+5.0f}% vol {x[1] * 100:3.0f}% Sh {x[2]:+.2f} DD {x[3] * 100:4.0f}%"
    print(f"{name:26s} | 2020-22 {f(a)} | 2023+ {f(b)} {extra}")


print("contratos:", len(syms), "días:", len(days))
print("activos operables por año:", {1970 + int(days[i] / DAY / 365.25): sum(active(s, i) for s in syms)
                                    for i in range(idx.get(START, 130), len(days), 365)})
btc = [(days[i + 1], R["BTCUSDT"][i + 1] or 0) for i in range(idx[START], len(days) - 1)]
line("BTC comprar y mantener", btc)
for n in (10, 20, 30, 60, 90, 120):
    GROSS.clear()
    rets, turn, flips = run_ts(n)
    line(f"TS {n}d, 20% vol, tope 2x", rets, f"| giro {turn:.0f}x/año, cambios {flips:.0f}/mes, "
                                            f"apalancamiento medio {statistics.mean(GROSS):.2f}x")
for lmax in (1.0, 3.0):
    rets, turn, flips = run_ts(30, lmax=lmax)
    line(f"TS 30d, 20% vol, tope {lmax:.0f}x", rets)
rets, *_ = run_ts(30, target=0.10)
line("TS 30d, 10% vol, tope 2x", rets)
rets, *_ = run_ts(30, rebalance=7)
line("TS 30d semanal", rets)
for n in (1, 3, 7, 14, 28):
    for mode in ("mom", "rev"):
        rets, turn = run_xs(n, mode)
        line(f"XS {mode} {n}d semanal", rets, f"| giro {turn:.0f}x/año")
for k in (3, 5, 10):
    GROSS.clear()
    rets, turn, flips = run_ts(30, topk=k)
    line(f"TS 30d, los {k} más fuertes", rets, f"| cambios {flips:.0f}/mes, apalancamiento medio "
                                              f"{statistics.mean(GROSS):.2f}x")
rets, *_ = run_ts(30, long_only=True)
line("TS 30d solo largos", rets)
best, *_ = run_ts(30)
print("TS 30d por año:", yearly(best))
