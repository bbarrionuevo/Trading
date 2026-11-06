"""¿En qué otros avisos de Upbit conviene invertir primero y que la IA lea después? (pedido del dueño, 5-nov). Los avisos
de trading de Upbit llegan por su canal oficial de Telegram a ~1 s del sitio (como los listados): fin del soporte
(corto), advertencia (corto), advertencia extendida (corto) y advertencia levantada (largo). Para cada uno, el perpetuo
de Binance con cada operación (data.binance.vision), entrando al instante (1,05 s) o esperando a la IA (2,85 s), con
stop fijo de 10 % y salida a los 15 min y a las 4 h, +0,1 % de deslizamiento y 0,2 % de costos.

Uso: python estudios/upbit_primero.py <carpeta con listados-upbit.json>
Baja un .zip por evento (se borra después de recortar la ventana; queda un .json chico en upbit_primero/).
"""
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
CACHE = D / "upbit_primero"                   # ventanas recortadas: datos, nunca código
CACHE.mkdir(exist_ok=True)
URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
KINDS = {"deslistado": -1, "alerta": -1, "alerta_extendida": -1, "alerta_levantada": 1}
COST, SLIP, STOP = 0.002, 0.001, 0.10
ENTRIES = {"al instante (1,05 s)": 1.05, "esperando a la IA (2,85 s)": 2.85}
HOLDS = (15 * 60, 4 * 3600)
AFTER_S = 4 * 3600 + 120


def fetch(symbol: str, day: str) -> bytes | None:
    try:
        with urllib.request.urlopen(URL.format(s=symbol, d=day), timeout=180) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    finally:
        time.sleep(0.5)


def window(ticker: str, t0_ms: int):
    for symbol in (f"{ticker}USDT", f"1000{ticker}USDT"):
        f = CACHE / f"{symbol}-{t0_ms}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    days = sorted({time.strftime("%Y-%m-%d", time.gmtime(t)) for t in (t0_ms / 1000, t0_ms / 1000 + AFTER_S)})
    for symbol in (f"{ticker}USDT", f"1000{ticker}USDT"):
        out, found = [], False
        for day in days:
            raw = fetch(symbol, day)
            if raw is None:
                continue
            found = True
            with zipfile.ZipFile(io.BytesIO(raw)) as z, z.open(z.namelist()[0]) as fh:
                last_s = -1
                for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                    if not row or not row[0].isdigit():
                        continue
                    t = int(row[5])
                    if t0_ms - 60_000 <= t <= t0_ms + AFTER_S * 1000:
                        if t > t0_ms + 120_000 and t // 1000 == last_s:
                            continue                       # después de los 2 min, una operación por segundo alcanza
                        last_s = t // 1000
                        out.append((t, float(row[1])))
        if found:
            out.sort()
            (CACHE / f"{symbol}-{t0_ms}.json").write_text(json.dumps(out), encoding="utf-8")
            return out
    return None


def trade(tr, t_in, side, hold_s):
    entry = next(((t, p) for t, p in tr if t >= t_in), None)
    if entry is None:
        return None
    px = entry[1] * (1 + SLIP * side)
    stop_px = px * (1 - STOP * side)
    end, last = entry[0] + hold_s * 1000, None
    for t, p in tr:
        if t <= entry[0]:
            continue
        if t > end:
            break
        if (p - stop_px) * side <= 0:
            return -STOP * (1 + SLIP) - COST
        last = p
    if last is None or (tr[-1][0] < end - 600_000):
        return None
    return (last / px - 1) * side - COST


def stats(xs):
    if not xs:
        return "n 0"
    return (f"n {len(xs):2d} · mediana {statistics.median(xs) * 100:+5.2f}% · media {statistics.mean(xs) * 100:+5.2f}% · "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}%")


events = [e for e in json.loads((D / "listados-upbit.json").read_text(encoding="utf-8"))["eventos"] if e["tipo"] in KINDS]
by_kind: dict[str, list] = {k: [] for k in KINDS}
for e in events:
    t0 = int(float(e["t"]) * 1000)
    tr = window(e["ticker"], t0)
    if not tr:
        continue
    pre = [p for t, p in tr if t < t0]
    if not pre or tr[0][0] > t0 - 1000:
        continue                                           # sin operaciones antes del aviso: no estaba en Binance
    ref = pre[-1]
    tr = [(t, p) for t, p in tr if ref / 3 < p < ref * 3]
    by_kind[e["tipo"]].append((t0, tr))
for kind, side in KINDS.items():
    rows = by_kind[kind]
    print(f"\n{kind} ({'corto' if side < 0 else 'largo'}): {len(rows)} con perpetuo en Binance")
    for name, s in ENTRIES.items():
        for hold in HOLDS:
            res = [x for t0, tr in rows if (x := trade(tr, t0 + s * 1000, side, hold)) is not None]
            print(f"  {name}, salida a {hold // 60:3d} min: {stats(res)}")
