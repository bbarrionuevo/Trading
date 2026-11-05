"""Listados de Upbit segundo a segundo en el perpetuo de Binance (archivo público data.binance.vision: operaciones de
cada día; la API solo guarda 2 días). ¿Cuánto del salto ya pasó a los 1-30 s del aviso, y cuánto gana entrar a los
~2 s (Tokio: Telegram 0-2 s después del sitio + 50 ms a Binance) contra ~9 s (la PC: sondeo de 8 s + 320 ms)?

Eventos: los listados (spot nuevo y alta en wones) de `listados-upbit.json` (estudio-listados --exchange upbit, con la
hora exacta del aviso). Solo los que ya tenían perpetuo en Binance ANTES del aviso (si no, no hay qué comprar).
Precio de entrada: la primera operación a los s segundos (+0,1 % de deslizamiento: el libro se vacía en el salto);
salida: la última operación antes de cada plazo; 0,2 % de costos ida y vuelta (comisión + deslizamiento de salida).

Uso: python estudios/upbit_segundos.py <carpeta con listados-upbit.json>
Baja un .zip por evento (se borra después de recortar la ventana; queda un .json chico por evento en la carpeta).
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
CACHE = D / "upbit_segundos"                  # ventanas recortadas: datos, nunca código
CACHE.mkdir(exist_ok=True)
URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
COST, SLIP = 0.002, 0.001
DELAYS = (1, 2, 3, 5, 9, 15, 30)
HORIZONS = (1, 5, 15, 60)                     # minutos después de entrar
KINDS = ("listado_spot", "listado_krw")
BEFORE_S, AFTER_S = 60, 3900                  # la ventana: 1 min antes, 65 min después


def fetch(symbol: str, day: str) -> bytes | None:
    try:
        with urllib.request.urlopen(URL.format(s=symbol, d=day), timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    finally:
        time.sleep(0.5)


def window(ticker: str, t0_ms: int) -> tuple[str, list] | None:
    """(símbolo, [(ms, precio)]) alrededor del aviso, o None si no había perpetuo ese día."""
    for symbol in (f"{ticker}USDT", f"1000{ticker}USDT"):
        f = CACHE / f"{symbol}-{t0_ms}.json"
        if f.exists():
            return symbol, json.loads(f.read_text(encoding="utf-8"))
    day = time.strftime("%Y-%m-%d", time.gmtime(t0_ms / 1000))
    for symbol in (f"{ticker}USDT", f"1000{ticker}USDT"):
        raw = fetch(symbol, day)
        if raw is None:
            continue
        out = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z, z.open(z.namelist()[0]) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                t = int(row[5])
                if t0_ms - BEFORE_S * 1000 <= t <= t0_ms + AFTER_S * 1000:
                    out.append((t, float(row[1])))
        out.sort()
        (CACHE / f"{symbol}-{t0_ms}.json").write_text(json.dumps(out), encoding="utf-8")
        return symbol, out
    return None


def first_at(tr, t):
    return next(((tt, p) for tt, p in tr if tt >= t), None)


def last_before(tr, t):
    best = None
    for tt, p in tr:
        if tt >= t:
            break
        best = (tt, p)
    return best


def summary(xs):
    if len(xs) < 5:
        return f"n {len(xs)}"
    return (f"n {len(xs):2d} mediana {statistics.median(xs) * 100:+6.2f}% media {statistics.mean(xs) * 100:+6.2f}% "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% peor {min(xs) * 100:+5.1f}% "
            f"mejor {max(xs) * 100:+5.1f}%")


events = [e for e in json.loads((D / "listados-upbit.json").read_text(encoding="utf-8"))["eventos"]
          if e.get("tipo") in KINDS]
rows, skipped = [], {"sin_perpetuo": 0, "sin_precio_previo": 0, "cruza_el_dia": 0}
for e in events:
    t0 = int(float(e["t"]) * 1000)
    if time.gmtime(t0 / 1000).tm_yday != time.gmtime(t0 / 1000 + AFTER_S).tm_yday:
        skipped["cruza_el_dia"] += 1                     # la ventana necesitaría dos archivos: se deja afuera
        continue
    w = window(e["ticker"], t0)
    if w is None:
        skipped["sin_perpetuo"] += 1
        continue
    symbol, tr = w
    pre = last_before(tr, t0)
    if pre is None or t0 - pre[0] > BEFORE_S * 1000:
        skipped["sin_precio_previo"] += 1                # sin operaciones antes del aviso: no estaba listado aún
        continue
    row = {"e": e, "symbol": symbol, "pre": pre[1], "tr": tr, "t0": t0}
    rows.append(row)
    jump = {s: (first_at(tr, t0 + s * 1000) or (0, pre[1]))[1] / pre[1] - 1 for s in (2, 9, 60)}
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t0 / 1000))} {e['tipo']:12} {symbol:14} "
          f"a los 2 s {jump[2] * 100:+5.1f}% · 9 s {jump[9] * 100:+5.1f}% · 60 s {jump[60] * 100:+5.1f}%")

print(f"\nlistados de Upbit (180 días): {len(events)} · con perpetuo en Binance antes del aviso: {len(rows)} · "
      f"afuera: {skipped}")
print("\nCuánto del salto ya pasó (desde el último precio antes del aviso):")
for s in DELAYS:
    mv = [p[1] / r["pre"] - 1 for r in rows if (p := first_at(r["tr"], r["t0"] + s * 1000))]
    print(f"  a los {s:2d} s: {summary(mv)}")
print("\nComprar a los s segundos y vender h minutos después (con costos):")
for s in DELAYS:
    for h in HORIZONS:
        res = []
        for r in rows:
            entry = first_at(r["tr"], r["t0"] + s * 1000)
            if entry is None:
                continue
            out = last_before(r["tr"], entry[0] + h * 60_000)
            if out is None or out[0] < entry[0] + h * 60_000 - 120_000:
                continue                                 # sin operaciones cerca del plazo: no se cuenta
            res.append(out[1] / (entry[1] * (1 + SLIP)) - 1 - COST)
        print(f"  entrar a los {s:2d} s, salir a los {h:2d} min: {summary(res)}")
for kind in KINDS:
    sub = [r for r in rows if r["e"]["tipo"] == kind]
    for s in (2, 9):
        res = []
        for r in sub:
            entry = first_at(r["tr"], r["t0"] + s * 1000)
            out = entry and last_before(r["tr"], entry[0] + 5 * 60_000)
            if entry and out:
                res.append(out[1] / (entry[1] * (1 + SLIP)) - 1 - COST)
        print(f"  {kind}: entrar a los {s} s, salir a los 5 min: {summary(res)}")
