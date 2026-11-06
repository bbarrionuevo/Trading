"""Listados spot de OKX y de Bybit en el perpetuo de Binance, segundo a segundo (pedido del dueño, 6-nov: "OKX y Bybit
(listados sorpresa en Spot) [...] pueden generar empujes del 5 % al 12 % muy operables"). El estudio del 3-nov
(docs/ESTUDIO-LISTADOS.md) tenía 4 casos por exchange, en MEXC y minuto a minuto: "no mueven el precio". Este usa todos
los de los últimos 12 meses con perpetuo en Binance ANTES del aviso, cada operación del perpetuo
(data.binance.vision) y la hora exacta de publicación de la API oficial de cada uno:

- OKX: `/api/v5/support/announcements?annType=announcements-new-listings` (pTime, en ms). "OKX to list ... for spot
  trading" y parecidos; no X-Perps, perpetuos, acciones tokenizadas ni pares en USDC de algo que ya lista.
- Bybit: `/v5/announcements/index?type=new_crypto` (publishTime, en ms), "Bybit to List Foo (FOO) on Spot" y "New
  Listing: FOO/USDT". Un mismo aviso puede aparecer dos veces (actualizado): cuenta el primero.
- Bithumb: su API de avisos (`api.bithumb.com/v1/notices`, la que usaría el agente en vivo: 130 ms desde Tokio) da solo
  los últimos 5. La historia sale de su página de avisos, categoría 9 "마켓 추가" (alta en el mercado en wones), que a
  los programas les da 403: se exporta con un navegador a `<carpeta>/bithumb-crudo.json` ([id, "AAAA-MM-DD HH:MM:SS"
  en hora de Corea, título]) y acá se pasa a UTC. "Foo(FOO) 원화 마켓 추가". En la consola del navegador, en
  https://feed.bithumb.com/notice: `b = __NEXT_DATA__.buildId; for (p = 1; p <= 4; p++) (await (await fetch(
  `/_next/data/${b}/notice.json?category=9&page=${p}`)).json()).pageProps.noticeList` → [id, publicationDateTime, title].
- Coinbase: el aviso de listado sale por X (API paga), sin historia accesible. Lo que sí es gratis y rápido es la
  página de estado del exchange (`status.exchange.coinbase.com`), que publica "FOO-USD Markets Open" cuando abre el
  mercado (la hora del incidente, en ms). Es la APERTURA, anunciada antes: se mide igual por si mueve el perpetuo.

Entrada: la primera operación a los s segundos (+0,1 % de deslizamiento). Salida: la última operación antes del plazo, o
la regla de los listados (stop fijo de 10 %, 15 min). 0,2 % de costos. Muestra el salto (+2 %) y cuándo empieza.

Resultado (6-nov; docs/ESTUDIO-LISTADOS.md): **Bithumb sí** (84 monedas: 77 saltan; comprar a 1 s del aviso, mediana
+4,29 %, media +7,09 %, 83 %; a 2 s, +2,32 %; en 2026 la ventana es de ~1 s). **OKX (4), Bybit (9) y la apertura de
Coinbase (31), no**: casi nunca saltan y comprar da alrededor de 0 o menos.

Uso: python estudios/otros_listados_segundos.py <okx|bybit|bithumb|coinbase> <carpeta de datos>   (en la PC: Tokio solo produce)
Guarda los avisos (listados-<exchange>-api.json) y una ventana chica por evento (ventanas/). Lee los .zip de a una fila.
"""
import bisect
import calendar
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
EXCHANGE = sys.argv[1]
D = Path(sys.argv[2])
D.mkdir(parents=True, exist_ok=True)
CACHE = D / "ventanas"                                # ventanas recortadas: datos, nunca código
CACHE.mkdir(exist_ok=True)
FAPI = "https://fapi.binance.com"
ZIP = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
SINCE_MS = int((time.time() - 365 * 86400) * 1000)    # los últimos 12 meses
COST, SLIP, STOP, HOLD_S = 0.002, 0.001, 0.10, 900
MOVES = (-30, 0, 1, 2, 3, 5, 10, 30, 60, 300)
DELAYS = (1, 2, 3, 5, 10, 30)
HORIZONS = (1, 5, 15, 60)
JUMP = 0.02
BEFORE_S, AFTER_S = 60, 3900
TICKER = re.compile(r"\(([A-Z0-9]{2,15})\)")
UA = {"User-Agent": "Mozilla/5.0 (estudio de TRADING, solo lectura)", "Accept": "application/json"}
SKIP = re.compile(r"x-perp|perpetual|futures|tokenized|stock|equity|margin|loan|earn|delist|migration|pre-market|"
                  r"launchpool|launchpad|jumpstart|convert|copy trading|options", re.I)


def get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def okx_announcements() -> list[dict]:
    out = []
    for page in range(1, 60):
        d = get_json(f"https://www.okx.com/api/v5/support/announcements?annType=announcements-new-listings&page={page}")
        det = [a for p in d.get("data") or [] for a in p.get("details") or []]
        if not det:
            break
        out += [{"title": a.get("title", ""), "t0": int(a.get("pTime") or 0)} for a in det]
        print(f"okx página {page}: {len(out)} avisos, hasta {time.strftime('%Y-%m-%d', time.gmtime(out[-1]['t0'] / 1000))}",
              flush=True)
        if out[-1]["t0"] < SINCE_MS:
            break
        time.sleep(1.5)
    return out


def bybit_announcements() -> list[dict]:
    out = []
    for page in range(1, 80):
        d = get_json(f"https://api.bybit.com/v5/announcements/index?locale=en-US&type=new_crypto&limit=50&page={page}")
        lst = (d.get("result") or {}).get("list") or []
        if not lst:
            break
        out += [{"title": a.get("title", ""), "t0": int(a.get("publishTime") or a.get("dateTimestamp") or 0),
                 "tags": a.get("tags") or []} for a in lst]
        print(f"bybit página {page}: {len(out)} avisos, hasta "
              f"{time.strftime('%Y-%m-%d', time.gmtime(out[-1]['t0'] / 1000))}", flush=True)
        if min(a["t0"] for a in out[-len(lst):]) < SINCE_MS:
            break
        time.sleep(1.5)
    return out


def bithumb_announcements() -> list[dict]:
    """La exportación de la página de avisos de Bithumb (hora de Corea, UTC+9, sin horario de verano) en UTC."""
    raw = json.loads((D / "bithumb-crudo.json").read_text(encoding="utf-8"))
    return [{"title": title, "t0": (calendar.timegm(time.strptime(kst, "%Y-%m-%d %H:%M:%S")) - 9 * 3600) * 1000}
            for _id, kst, title in raw]


def coinbase_opens() -> list[dict]:
    """Las aperturas de mercado de la página de estado de Coinbase Exchange (12 meses de historia, cada incidente con su
    hora exacta de creación)."""
    out = []
    for page in range(1, 6):
        d = get_json(f"https://status.exchange.coinbase.com/history.json?page={page}")
        for m in d.get("months") or []:
            for i in m.get("incidents") or []:
                if re.match(r"^[A-Z0-9]{2,15}-USD Markets Open$", i.get("name", "")):
                    time.sleep(1.0)
                    inc = get_json(f"https://status.exchange.coinbase.com/api/v2/incidents/{i['code']}.json")
                    created = (inc.get("incident") or {}).get("created_at")
                    if created:
                        ts = calendar.timegm(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))   # la hora local de ahí
                        off = created[23:] if len(created) > 23 else "+00:00"      # "-08:00" / "-07:00"
                        sign = -1 if off.startswith("-") else 1
                        utc = ts - sign * (int(off[1:3]) * 3600 + int(off[4:6]) * 60)
                        out.append({"title": i["name"], "t0": int(utc * 1000) + int(created[20:23] or 0)})
        print(f"coinbase página {page}: {len(out)} aperturas", flush=True)
        time.sleep(1.0)
    return out


def spot_coins(title: str) -> tuple[str, ...]:
    """Las monedas de un listado spot (las reglas de título de cada exchange); nada si es otro aviso."""
    if EXCHANGE == "coinbase":
        m = re.match(r"^([A-Z0-9]{2,15})-USD Markets Open$", title)
        return (m.group(1),) if m and "USD" not in m.group(1) else ()
    if EXCHANGE == "bithumb":
        if "원화 마켓 추가" not in title:
            return ()
        return tuple(dict.fromkeys(c for c in TICKER.findall(title) if "USD" not in c and c not in ("EURC", "XAUT")))
    if SKIP.search(title):
        return ()
    if EXCHANGE == "okx":
        if not re.search(r"\bOKX (?:to|will) list\b", title, re.I):
            return ()
        if re.search(r"USDC trading pairs? for", title, re.I):        # un par más de algo que ya lista
            return ()
        got = TICKER.findall(title)
        if not got:                                                   # "OKX to list FOO for spot trading"
            m = re.search(r"\blist (?:the )?([A-Z0-9]{2,15}) (?:\(|for spot|spot)", title)
            got = [m.group(1)] if m else []
    else:
        m = re.search(r"New Listing:\s*([A-Z0-9]{2,15})/USDT", title, re.I)
        got = [m.group(1).upper()] if m else (TICKER.findall(title) if re.search(r"\bon Spot\b", title, re.I) else [])
    return tuple(dict.fromkeys(c for c in got if "USD" not in c))


def announcements() -> list[dict]:
    f = D / f"listados-{EXCHANGE}-api.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    if EXCHANGE == "bithumb":
        arts = bithumb_announcements()                 # la exportación ya marca el período (sep-2025 a nov-2026)
    elif EXCHANGE == "coinbase":
        arts = coinbase_opens()
    else:
        arts = okx_announcements() if EXCHANGE == "okx" else bybit_announcements()
        arts = [a for a in arts if a["t0"] >= SINCE_MS]
    f.write_text(json.dumps(arts, ensure_ascii=False), encoding="utf-8")
    return arts


def fetch_day(symbol: str, day: str, t_from: int, t_to: int) -> list | None:
    tmp = CACHE / f"_{symbol}-{day}.zip"
    for attempt in range(4):
        try:
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


class Tape:
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
        got = self.first_at(t_in)
        if got is None:
            return None
        i = bisect.bisect_right(self.t, got[0])
        px, end, last = got[1] * (1 + SLIP), got[0] + HOLD_S * 1000, None
        while i < len(self.t) and self.t[i] <= end:
            if self.p[i] <= px * (1 - STOP):
                return self.p[i] * (1 - SLIP) / px - 1 - COST
            last = self.p[i]
            i += 1
        return None if last is None else last / px - 1 - COST


def numbers(tape: Tape, t0: int, pre: float) -> dict:
    mv = {s: (p[1] / pre - 1 if (p := tape.first_at(t0 + s * 1000)) else None) for s in MOVES}
    ret = {}
    for s in DELAYS:
        entry = tape.first_at(t0 + s * 1000)
        for h in HORIZONS:
            out = tape.last_before(entry[0] + h * 60_000) if entry else None
            ok = out is not None and out[0] >= entry[0] + h * 60_000 - 120_000
            ret[(s, h)] = out[1] / (entry[1] * (1 + SLIP)) - 1 - COST if ok else None
        ret[(s, "regla")] = tape.rule(t0 + s * 1000)
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
    print(f"  saltó (+2 %) en el primer minuto: {len(got)} de {len(rows)}"
          + (f"; empieza a los {statistics.median(got):.2f} s (mediana)" if got else ""))
    for s in MOVES:
        print(f"  el perpetuo ya se movió a los {s:>4} s: {summary([r['mv'][s] for r in rows])}")
    for s in DELAYS:
        for h in (*HORIZONS, "regla"):
            label = "stop 10 % o 15 min" if h == "regla" else f"vender a los {h:2d} min"
            print(f"  comprar a los {s:>2} s, {label}: {summary([r['ret'][(s, h)] for r in rows])}")


arts = announcements()
print(f"\navisos de {EXCHANGE} en 12 meses: {len(arts)}")
info = get_json(FAPI + "/fapi/v1/exchangeInfo")
onboard = {x["symbol"]: int(x.get("onboardDate") or 0) for x in info["symbols"]
           if x.get("quoteAsset") == "USDT" and x.get("contractType") == "PERPETUAL"}
first: dict[str, dict] = {}                           # cada moneda, con su primer aviso (los actualizados se repiten)
for a in sorted(arts, key=lambda a: a["t0"]):
    for c in spot_coins(a["title"]):
        first.setdefault(c, a)
print(f"monedas con aviso de listado spot: {len(first)}")
rows, skipped = [], {"sin_perpetuo_antes": 0, "sin_datos": 0}
for coin, a in sorted(first.items(), key=lambda kv: kv[1]["t0"]):
    perp = next((p for p in (f"{coin}USDT", f"1000{coin}USDT") if p in onboard), None)
    if not perp or onboard[perp] >= a["t0"] - 60_000:
        skipped["sin_perpetuo_antes"] += 1
        continue
    tr = window(perp, a["t0"])
    tape = Tape(tr) if tr else None
    pre = tape.last_before(a["t0"]) if tape else None
    if not pre or a["t0"] - pre[0] > BEFORE_S * 1000:
        skipped["sin_datos"] += 1
        continue
    got = numbers(tape, a["t0"], pre[1])
    rows.append({"t0": a["t0"], "perp": perp, **got})
    mv = got["mv"]
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(a['t0'] / 1000))} {perp:14} 2 s {(mv[2] or 0) * 100:+5.1f}% · "
          f"10 s {(mv[10] or 0) * 100:+5.1f}% · 60 s {(mv[60] or 0) * 100:+5.1f}% · 5 min {(mv[300] or 0) * 100:+5.1f}% · "
          f"salto {'-' if got['onset'] is None else format(got['onset'], '.1f')} s  {a['title'][:60]}", flush=True)
    del tr, tape
print(f"\nafuera: {skipped}")
report(f"{EXCHANGE}: desde el aviso", rows)
half = len(rows) // 2
report(f"{EXCHANGE}: primera mitad", rows[:half])
report(f"{EXCHANGE}: segunda mitad", rows[half:])
