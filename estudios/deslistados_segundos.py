"""Deslistados de Binance segundo a segundo, con el archivo público de Binance (data.binance.vision: operaciones de
cada día; la API solo guarda 2 días). Para los avisos desde 2025 de deslistados.json: el precio justo antes del
aviso y el de entrada a los 2, 5, 10 y 30 s (el agente recibe el aviso por el canal de Binance en ~1 s); la salida,
con las velas del perpetuo, stop fijo, funding real y 0,2 % de costos.
Uso: python estudios/deslistados_segundos.py <carpeta con deslistados.json>"""
import csv
import io
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1])
ZIPS = D / "aggtrades"                       # archivos bajados: datos, nunca código
ZIPS.mkdir(exist_ok=True)
MIN, HOUR = 60_000, 3_600_000
COST = 0.002
URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"


def window(e):
    day = time.strftime("%Y-%m-%d", time.gmtime(e["t0"] / 1000))
    f = ZIPS / f"{e['symbol']}-{day}.zip"
    if not f.exists():
        try:
            with urllib.request.urlopen(URL.format(s=e["symbol"], d=day), timeout=60) as r:
                f.write_bytes(r.read())
        except (urllib.error.URLError, TimeoutError):
            return None
        time.sleep(0.3)
    out = []
    with zipfile.ZipFile(f) as z:
        with z.open(z.namelist()[0]) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                t = int(row[5])
                if e["t0"] - 5000 <= t <= e["t0"] + 120_000:
                    out.append((t, float(row[1])))
    return sorted(out)


def exit_from(e, entry, t_in, stop, hold_h):
    t_out = t_in + hold_h * HOUR
    m1 = [b for b in e["m1"] if t_in <= b[0] < t_out]
    end = e["m1"][-1][0] + MIN
    path = [(b[0], b[2], b[4]) for b in m1 + [b for b in e["h1"] if end <= b[0] < t_out]]
    if not path or path[-1][0] < t_out - 2 * HOUR:
        return None
    out, t_end = path[-1][2], t_out
    for t, h, _c in path:
        if h >= entry * (1 + stop):
            out, t_end = entry * (1 + stop) * 1.002, t + MIN
            break
    fund = sum(r for ft, r in e["funding"] if t_in < ft <= t_end)
    return -(out / entry - 1) + fund - COST


def summary(xs):
    if len(xs) < 8:
        return f"n {len(xs)}"
    return (f"n {len(xs):2d} mediana {statistics.median(xs) * 100:+5.1f}% media {statistics.mean(xs) * 100:+5.1f}% "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% peor {min(xs) * 100:+4.0f}%")


NAME = sys.argv[2] if len(sys.argv) > 2 else "deslistados.json"
events = [e for e in json.loads((D / NAME).read_text(encoding="utf-8")) if e["t0"] >= 1735689600000]
rows = []
for e in events:
    tr = window(e)
    if not tr:
        continue
    pre = [p for t, p in tr if t < e["t0"]]
    if not pre:
        continue
    row = {"e": e, "pre": pre[-1]}
    for s in (2, 5, 10, 30, 60):
        after = [(t, p) for t, p in tr if t >= e["t0"] + s * 1000]
        if after:
            row[s] = after[0]
    rows.append(row)
print("avisos desde 2025 con operaciones:", len(rows), "de", len(events))
for s in (2, 5, 10, 30, 60):
    mv = [r[s][1] / r["pre"] - 1 for r in rows if s in r]
    print(f"caída a los {s:2d} s (mediana): {statistics.median(mv) * 100:+.1f}%  (n {len(mv)})")
for s in (2, 5, 10):
    for hold in (1, 4, 24, 72):
        for stop in (0.10, 0.20):
            res = [x for r in rows if s in r and (x := exit_from(r["e"], r[s][1], r[s][0], stop, hold)) is not None]
            print(f"corto a los {s:2d} s, {hold:2d} h, stop {stop * 100:.0f} %: {summary(res)}")
