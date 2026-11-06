"""Listados de Binance en el perpetuo, segundo a segundo (pedido del dueño, 6-nov: "el retraso Binance Spot ->
Perpetuo (el módulo de 10 s) ... 10 segundos es una eternidad para mi servidor en Tokio"). Dos momentos por listado:

A. El AVISO ("Binance Will List X", HODLer Airdrops): llega a Tokio por el canal de anuncios en 1,2-2,6 s; con la IA,
   ~3-4,5 s. ¿Cuánto se mueve el perpetuo a los 1-60 s del aviso, y cuánto gana entrar a los 2, 3, 5 o 10 s?
B. La APERTURA del spot (a una hora que el aviso dice de antemano: no hay carrera por enterarse): ¿el perpetuo va
   detrás del spot unos segundos? Spot: velas de 1 s de Binance; perpetuo: cada operación (data.binance.vision).

Solo los listados cuyo perpetuo USDT ya existía antes de ese momento (si no, no hay qué comprar). Entrada: la primera
operación a los s segundos (+0,1 % de deslizamiento); salida: la última operación antes del plazo, o la regla de los
listados de Upbit (stop fijo de 10 %, 15 min); 0,2 % de costos.

1. Los avisos del catálogo 48 del CMS público de Binance desde el 1-ene-2025 (50 por página, un pedido cada 30 s y se
   corta al primer 429: REGLAS L-7).
2. La apertura del spot: la primera vela de 1 minuto (y de 1 s) del par XYZUSDT después del aviso.

Resultado (6-nov; 66 monedas de 58 avisos, ene-2025 a oct-2026; la hora del CMS viene con milisegundos):
- No hay 10 s, y no es en la apertura del spot: es en el AVISO. El perpetuo salta (+2 %) a los 2,30 s de publicado
  (mediana; la mitad central entre 2,15 y 2,58 s; 43 de 66 entre los 2 y los 2,5 s). A los 2 s, nada (+0,02 %); a los
  2,5 s, +3,9 %; a los 3,5 s, +9,0 %.
- Comprar a los 2 s con la regla de Upbit (stop 10 %, 15 min): mediana +7,50 %, media +18,58 %, 77 % positivas (1.ª
  mitad +13,8 %, 2.ª +4,1 % de mediana; listados y HODLer, las dos). Cada 100 ms después cuesta: 2,2 s +6,1 %;
  2,5 s +2,0 %; 3 s +0,4 %; 3,5 s (con la IA) −0,5 %.
- En la apertura del spot el perpetuo no sigue al spot (a los 10 s, −0,15 %); comprarlo ahí pierde (mediana −1,6 %).
- El canal de anuncios llega a Tokio a los 1,2-1,8 s (5 avisos; uno a hora redonda, 2,6 s; un TradFi programado, 41 s):
  hay que medirlo con listados antes de operar.

Uso: python estudios/binance_listados_segundos.py <carpeta de datos>   (en la PC: Tokio solo produce)
Guarda los avisos (listados-binance-cms.json) y una ventana chica por evento. Cuida la memoria: el .zip del día va a un
archivo temporal y se lee de a una fila, y de cada ventana quedan solo sus números.
"""
import bisect
import csv
import io
import json
import re
import shutil
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1])
D.mkdir(parents=True, exist_ok=True)
CACHE = D / "ventanas"                                # ventanas recortadas: datos, nunca código
CACHE.mkdir(exist_ok=True)
CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
FAPI, SPOT = "https://fapi.binance.com", "https://api.binance.com"
ZIP = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
SINCE_MS = 1735689600000                              # 1-ene-2025
COST, SLIP, STOP, HOLD_S = 0.002, 0.001, 0.10, 900
MOVES = (-30, 0, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 10, 20, 30, 60)
DELAYS = (1.5, 2, 2.5, 3, 3.5, 4, 5, 10, 30)
FINE = (1.5, 1.8, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.8, 3.0, 3.5, 4.0)   # cada 100 ms alrededor del salto
JUMP = 0.02                                          # el salto empieza cuando el perpetuo ya subió 2 %
HORIZONS = (1, 5, 15, 60)
BEFORE_S, AFTER_S = 60, 3900
TICKER = re.compile(r"\(([A-Z0-9]{2,15})\)")
UA = {"User-Agent": "Mozilla/5.0 (estudio de TRADING, solo lectura)", "Accept": "application/json"}


def kind(title: str) -> str:
    t = title.lower()
    if "futures will launch" in t or ("perpetual" in t and "will launch" in t):
        return "lanzamiento_futuros"
    if "hodler airdrop" in t:
        return "hodler_airdrop"
    if "will list" in t:
        return "listado_spot"
    if "will add" in t:
        return "agrega_productos"
    if "alpha" in t:
        return "alpha"
    return "otro"


def get_json(url: str, **params):
    q = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    with urllib.request.urlopen(urllib.request.Request(q, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def announcements() -> list[dict]:
    f = D / "listados-binance-cms.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    arts = []
    for page in range(1, 60):
        try:
            d = get_json(CMS, type=1, catalogId=48, pageNo=page, pageSize=50)
        except urllib.error.HTTPError as e:
            print("el CMS contestó", e.code, "en la página", page, "- corto acá", flush=True)
            break
        items = (((d or {}).get("data") or {}).get("catalogs") or [{}])[0].get("articles") or []
        if not items:
            break
        arts += [{"title": a.get("title", ""), "t0": int(a.get("releaseDate") or 0)} for a in items]
        print(f"página {page}: {len(arts)} avisos, hasta {time.strftime('%Y-%m-%d', time.gmtime(arts[-1]['t0'] / 1000))}",
              flush=True)
        if arts[-1]["t0"] < SINCE_MS:
            break
        time.sleep(30.0)                              # L-7: www.binance.com, como mucho una vez cada 30 s
    arts = [a for a in arts if a["t0"] >= SINCE_MS]
    f.write_text(json.dumps(arts, ensure_ascii=False), encoding="utf-8")
    return arts


def fetch_day(symbol: str, day: str, t_from: int, t_to: int) -> list | None:
    """Las operaciones del día entre t_from y t_to. El .zip va a un archivo temporal y se lee de a una fila (el día de
    una moneda movida pesa cientos de MB: nunca entero en memoria); las filas vienen en orden de hora."""
    tmp = CACHE / f"_{symbol}-{day}.zip"
    for attempt in range(4):                          # el servidor a veces corta la conexión: 4 intentos y, si no,
        try:                                          # el evento queda afuera (sin datos) y no se guarda nada
            with urllib.request.urlopen(ZIP.format(s=symbol, d=day), timeout=300) as r, tmp.open("wb") as fh:
                shutil.copyfileobj(r, fh, 1 << 20)
            break
        except urllib.error.HTTPError as e:
            tmp.unlink(missing_ok=True)
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except OSError:
            tmp.unlink(missing_ok=True)
            if attempt == 3:
                return None
        time.sleep(5.0 * (attempt + 1))
    time.sleep(0.3)
    out = []
    try:
        with zipfile.ZipFile(tmp) as z, z.open(z.namelist()[0]) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                t = int(row[5])
                if t > t_to:
                    break
                if t >= t_from:
                    out.append((t, float(row[1])))
    finally:
        tmp.unlink(missing_ok=True)
    return out


def window(symbol: str, t0: int) -> list | None:
    """Cada operación del perpetuo entre 1 min antes y 65 min después de t0 (de uno o dos archivos diarios)."""
    f = CACHE / f"{symbol}-{t0}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    t_from, t_to = t0 - BEFORE_S * 1000, t0 + AFTER_S * 1000
    out = []
    for day in sorted({time.strftime("%Y-%m-%d", time.gmtime(x / 1000)) for x in (t_from, t_to)}):
        got = fetch_day(symbol, day, t_from, t_to)
        if got is None:
            return None
        out += got
    out.sort()
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def spot_open(tk: str, t_ann: int) -> tuple[int, list] | None:
    """(hora de la primera operación del spot XYZUSDT, velas de 1 s de sus primeros 120 s) si el par abrió DESPUÉS del
    aviso; None si ya cotizaba antes (no es una apertura) o no abrió."""
    sym = f"{tk}USDT"
    f = CACHE / f"spot-{sym}-{t_ann}.json"
    if f.exists():
        got = json.loads(f.read_text(encoding="utf-8"))
        return (got[0], got[1]) if got else None
    res = None
    try:
        first = get_json(SPOT + "/api/v3/klines", symbol=sym, interval="1d", startTime=0, limit=1)
        if first and int(first[0][0]) >= t_ann - 86_400_000:   # el par nació cerca o después del aviso
            m1 = get_json(SPOT + "/api/v3/klines", symbol=sym, interval="1m", startTime=t_ann, limit=1000)
            if m1 and int(m1[0][0]) > t_ann:
                s1 = get_json(SPOT + "/api/v3/klines", symbol=sym, interval="1s", startTime=int(m1[0][0]), limit=180)
                if s1:
                    t_open = int(s1[0][0])
                    res = (t_open, [(int(k[0]), float(k[1]), float(k[2]), float(k[4])) for k in s1
                                    if int(k[0]) < t_open + 120_000])
    except urllib.error.HTTPError:
        res = None
    time.sleep(0.25)
    f.write_text(json.dumps(list(res) if res else []), encoding="utf-8")
    return res


class Tape:
    """Las operaciones de una ventana, con búsqueda por hora."""

    def __init__(self, tr):
        self.t = [t for t, _ in tr]
        self.p = [p for _, p in tr]

    def first_at(self, t):
        i = bisect.bisect_left(self.t, t)
        return (self.t[i], self.p[i]) if i < len(self.t) else None

    def last_before(self, t):
        i = bisect.bisect_left(self.t, t) - 1
        return (self.t[i], self.p[i]) if i >= 0 else None

    def rule(self, t_in):
        """La regla de los listados de Upbit: stop fijo de 10 % (se vende a la operación que lo cruza, −0,1 %) o 15 min."""
        got = self.first_at(t_in)
        if got is None:
            return None
        i = bisect.bisect_right(self.t, got[0])
        px, end = got[1] * (1 + SLIP), got[0] + HOLD_S * 1000
        last = None
        while i < len(self.t) and self.t[i] <= end:
            if self.p[i] <= px * (1 - STOP):
                return self.p[i] * (1 - SLIP) / px - 1 - COST
            last = self.p[i]
            i += 1
        return None if last is None else last / px - 1 - COST


def numbers(tape: Tape, t0: int, pre: float) -> dict:
    """De cada ventana quedan solo sus números: cuánto se movió a cada segundo y cuánto da cada entrada y salida."""
    mv = {s: (p[1] / pre - 1 if (p := tape.first_at(t0 + s * 1000)) else None) for s in MOVES}
    ret = {}
    for s in DELAYS:
        entry = tape.first_at(t0 + s * 1000)
        for h in HORIZONS:
            out = tape.last_before(entry[0] + h * 60_000) if entry else None
            ok = out is not None and out[0] >= entry[0] + h * 60_000 - 120_000
            ret[(s, h)] = out[1] / (entry[1] * (1 + SLIP)) - 1 - COST if ok else None
        ret[(s, "regla")] = tape.rule(t0 + s * 1000)
    for s in FINE:
        ret[("fino", s)] = tape.rule(t0 + s * 1000)
    i = bisect.bisect_left(tape.t, t0)
    onset = next(((tape.t[j] - t0) / 1000 for j in range(i, len(tape.t))
                  if tape.t[j] <= t0 + 60_000 and tape.p[j] >= pre * (1 + JUMP)), None)
    return {"mv": mv, "ret": ret, "onset": onset}


def summary(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 5:
        return f"n {len(xs)}" + (f" ({', '.join(f'{x * 100:+.1f}%' for x in xs)})" if xs else "")
    return (f"n {len(xs):2d} mediana {statistics.median(xs) * 100:+6.2f}% media {statistics.mean(xs) * 100:+6.2f}% "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% peor {min(xs) * 100:+5.1f}% "
            f"mejor {max(xs) * 100:+5.1f}%")


def report(name: str, rows: list) -> None:
    print(f"\n== {name} (n {len(rows)}) ==")
    on = [r["onset"] for r in rows]
    got = sorted(x for x in on if x is not None)
    if len(got) >= 4:
        q = statistics.quantiles(got, n=4)
        print(f"  el salto (+2 %) empieza a los {statistics.median(got):.2f} s del aviso (mediana; cuartos {q[0]:.2f} y "
              f"{q[2]:.2f} s); sin salto en el primer minuto: {on.count(None)}")
        for lo, hi in ((0, 1), (1, 1.5), (1.5, 2), (2, 2.5), (2.5, 3), (3, 4), (4, 5), (5, 10), (10, 60)):
            k = sum(lo <= x < hi for x in got)
            print(f"    entre {lo:4} y {hi:4} s: {k:3d} {'#' * k}")
    for s in MOVES:
        print(f"  el perpetuo ya se movió a los {s:>4} s: {summary([r['mv'][s] for r in rows])}")
    for s in DELAYS:
        for h in (*HORIZONS, "regla"):
            label = "stop 10 % o 15 min" if h == "regla" else f"vender a los {h:2d} min"
            print(f"  comprar a los {s:>4} s, {label}: {summary([r['ret'][(s, h)] for r in rows])}")


arts = announcements()
print(f"\navisos del catálogo de listados desde 2025: {len(arts)}")
print("por tipo:", {k: sum(kind(a['title']) == k for a in arts) for k in sorted({kind(a['title']) for a in arts})})
info = get_json(FAPI + "/fapi/v1/exchangeInfo")
onboard = {x["symbol"]: int(x.get("onboardDate") or 0) for x in info["symbols"]
           if x.get("quoteAsset") == "USDT" and x.get("contractType") in ("PERPETUAL", "TRADIFI_PERPETUAL")}
ann_rows, open_rows, lags = [], [], []
skipped = {"sin_perpetuo_al_aviso": 0, "sin_perpetuo_a_la_apertura": 0, "sin_apertura_del_spot": 0, "sin_datos": 0}
for a in sorted(arts, key=lambda a: a["t0"]):
    k = kind(a["title"])
    if k not in ("listado_spot", "hodler_airdrop"):
        continue
    for tk in dict.fromkeys(TICKER.findall(a["title"])):
        perp = next((c for c in (f"{tk}USDT", f"1000{tk}USDT") if c in onboard), None)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(a["t0"] / 1000))
        # A. el aviso
        if perp and onboard[perp] < a["t0"] - 60_000:
            tr = window(perp, a["t0"])
            tape = Tape(tr) if tr else None
            pre = tape.last_before(a["t0"]) if tape else None
            if pre and a["t0"] - pre[0] <= BEFORE_S * 1000:
                got = numbers(tape, a["t0"], pre[1])
                ann_rows.append({"k": k, "t0": a["t0"], "perp": perp, **got})
                mv = got["mv"]
                print(f"{stamp} AVISO    {k:14} {perp:14} −30 s {(mv[-30] or 0) * 100:+5.1f}% · 2 s "
                      f"{(mv[2] or 0) * 100:+5.1f}% · 5 s {(mv[5] or 0) * 100:+5.1f}% · 10 s {(mv[10] or 0) * 100:+5.1f}% "
                      f"· 60 s {(mv[60] or 0) * 100:+5.1f}% · salto a los "
                      f"{'-' if got['onset'] is None else format(got['onset'], '.2f')} s  {a['title'][:50]}", flush=True)
            else:
                skipped["sin_datos"] += 1
            del tr, tape
        else:
            skipped["sin_perpetuo_al_aviso"] += 1
        # B. la apertura del spot
        op = spot_open(tk, a["t0"])
        if op is None:
            skipped["sin_apertura_del_spot"] += 1
            continue
        t_open, s1 = op
        if not perp or onboard[perp] >= t_open - 60_000:
            skipped["sin_perpetuo_a_la_apertura"] += 1
            continue
        tr = window(perp, t_open)
        tape = Tape(tr) if tr else None
        pre = tape.last_before(t_open) if tape else None
        if not pre or t_open - pre[0] > BEFORE_S * 1000:
            skipped["sin_datos"] += 1
            continue
        open_rows.append({"k": k, "t0": t_open, "perp": perp, **numbers(tape, t_open, pre[1])})
        spot_first = s1[0][1]
        spot_at = {s: next((c for t, _o, _h, c in s1 if t >= t_open + (s - 1) * 1000), s1[-1][3]) for s in (1, 5, 10, 30)}
        perp_at = {s: (tape.first_at(t_open + s * 1000) or (0, pre[1]))[1] for s in (1, 5, 10, 30)}
        lags.append({"spot_open_vs_perp": spot_first / pre[1] - 1,
                     **{f"spot{s}": spot_at[s] / pre[1] - 1 for s in spot_at},
                     **{f"perp{s}": perp_at[s] / pre[1] - 1 for s in perp_at}})
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t_open / 1000))} APERTURA {k:14} {perp:14} spot abre "
              f"{(spot_first / pre[1] - 1) * 100:+6.1f}% vs perpetuo · a los 10 s: spot {lags[-1]['spot10'] * 100:+6.1f}% "
              f"perpetuo {lags[-1]['perp10'] * 100:+6.1f}%", flush=True)
        del tr, tape

print(f"\nafuera: {skipped}")
report("A. desde el AVISO", ann_rows)
for k in ("listado_spot", "hodler_airdrop"):
    report(f"A. desde el AVISO, solo {k}", [r for r in ann_rows if r["k"] == k])
half = len(ann_rows) // 2
report("A. desde el AVISO, primera mitad (por fecha)", ann_rows[:half])
report("A. desde el AVISO, segunda mitad (por fecha)", ann_rows[half:])
report("B. desde la APERTURA del spot", open_rows)
print(f"\n== A. Cada 100 ms: comprar a los s segundos del aviso, stop 10 % o 15 min (n {len(ann_rows)}) ==")
for s in FINE:
    print(f"  a los {s:.1f} s: {summary([r['ret'][('fino', s)] for r in ann_rows])}")
ms = [r["t0"] % 1000 for r in ann_rows]
print(f"hora de publicación del CMS: {sum(m == 0 for m in ms)} de {len(ms)} redondeadas al segundo")
if lags:
    print("\n¿El perpetuo va detrás del spot? (medianas, contra el perpetuo justo antes de abrir)")
    print(f"  el spot abre {statistics.median([x['spot_open_vs_perp'] for x in lags]) * 100:+.2f}% contra el perpetuo")
    for s in (1, 5, 10, 30):
        print(f"  a los {s:2d} s: spot {statistics.median([x[f'spot{s}'] for x in lags]) * 100:+6.2f}% · perpetuo "
              f"{statistics.median([x[f'perp{s}'] for x in lags]) * 100:+6.2f}%")
