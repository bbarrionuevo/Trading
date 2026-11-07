"""Tres hipótesis direccionales del dueño (7-nov: "Quiero rentabilizar al máximo el tiempo de inactividad del servidor
[...] Necesito que filtres tu base de datos histórica de eventos y corras los números reales para cada una"):

1. **deslistados**: corto en el perpetuo cuando Binance anuncia que deslista una moneda ("Binance Will Delist", el
   deslistado del propio perpetuo; la etiqueta de vigilancia para comparar). Catálogo 161 del CMS desde 2025.
2. **launchpool**: largo en BNB cuando Binance anuncia un proyecto de Launchpool (Megadrop y HODLer Airdrops para
   comparar). Catálogo 48 desde 2024.
3. **advertencias**: corto en el perpetuo cuando Upbit o Bithumb designan una moneda "de advertencia" (유의 종목 지정,
   거래유의종목 지정: la de DAXA es la misma), corto en el fin del soporte y largo cuando la levantan. Upbit: su API de
   avisos (desde la PC); Bithumb: la exportación de su página (bithumb-avisos.json, con un navegador).

Para cada una: (1) rendimiento neto y acierto entrando a distintas demoras (0,1 s, la que pidió el dueño, y las reales
de Tokio) con salidas fijas de 45 s a 4 h y stops que siguen al pico; (2) cuándo llega el pico (MFE) en 15 min, 1 h y
4 h; (3) la salida que más rinde y si se sostiene en las dos mitades; (4) liquidez y deslizamiento: cuánto se movió
antes de entrar, cuánto barre una orden a mercado en 200 ms, cuántas veces se llena una límite IOC con tope de 0,25 a
5 % y cuánto se operó en el primer minuto.

Precios: cada operación del perpetuo de Binance (data.binance.vision, 1 min antes y 65 min después) y velas de 1 min
del perpetuo hasta las 4 h (también data.binance.vision: sirve para los perpetuos ya deslistados). BNB, con velas de 1 s
y de 1 min del spot (su archivo diario de operaciones pesa demasiado y el perpetuo sigue al spot). 0,2 % de costos ida y
vuelta; el deslizamiento se mide aparte (punto 4).

Resultado (7-nov; docs/TRASPASO.md §12):
- Deslistados de Binance (49): cae entre los 3 y los 5 s; entrar a 0,1, 1,3 o 2 s da lo mismo. A las 4 h: mediana
  +17,96 %, 84 %; a los 60 s +7,50 %; los stops que siguen al pico rinden mucho menos. IOC con 0,5 %: 33 de 49.
- BNB por Launchpool (30): salta entre los 2 y los 3 s; antes de eso, +0,8 % de media; a los 2,7 s, nada. Rara (9 desde
  2025). HODLer: nada.
- Advertencia de Upbit (42): sin salto, baja de a poco; a las 4 h mediana +2,02 %, 69 %. Bithumb (43): +0,90 %, 53 %. Su
  fin del soporte: Upbit (16) a los 5 min +4,59 %, 88 %; Bithumb (20) +1,33 %, 75 %.
- 5. Como la opera el agente (7-nov, decision/korean_notices.py y el tope de la orden): el fin del soporte con el
  PRIMER aviso de Upbit o Bithumb (25 monedas; en las 11 conjuntas Bithumb publicó primero o junto): a los 2 s, 5 min,
  mediana +3,73 %, 80 %. El tope de la límite IOC: en el deslistado (freno de 4 %, orden a los 2 s) 0,5 % se llena 33 de
  45 (+7,2 % por aviso) y 2 %, 43 (+12,3 %): las que faltaban eran las mejores. En lo coreano, 0,5 % ya alcanza.

Uso: python estudios/hipotesis_direccionales.py <deslistados|launchpool|advertencias> <carpeta de datos>
(en la PC: Tokio solo produce; el CMS de Binance, una página cada 30 s: L-7).
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
HYP, D = (sys.argv[1], Path(sys.argv[2])) if len(sys.argv) >= 3 else ("advertencias", Path("."))
CACHE = D / "ventanas"                                # datos, nunca código
UA = {"User-Agent": "Mozilla/5.0 (estudio de TRADING, solo lectura)", "Accept": "application/json"}
CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
AGG = "https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
K1M = "https://data.binance.vision/data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip"
UPBIT = "https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade"
COST = 0.002
EXITS_S = (45, 60, 120, 300, 900, 1800, 3600, 14400)
CFG = {"deslistados": {"delays": (0.1, 1.3, 2.0, 2.7, 5.0), "real": 2.0, "stop": 0.20, "trails": (0.03, 0.05, 0.08)},
       "launchpool": {"delays": (0.1, 1.3, 2.0, 2.7, 5.0), "real": 2.0, "stop": 0.03, "trails": (0.005, 0.01, 0.02)},
       "advertencias": {"delays": (0.1, 1.0, 2.0, 3.0, 5.0), "real": 2.0, "stop": 0.10, "trails": (0.03, 0.05, 0.08)},
       # 7-nov (segunda tanda del dueño), con nuestras demoras medidas: el canal de anuncios de Binance llega a 1,2 s
       # (a 2,6 s si se publica a hora redonda) y Upbit por Telegram a ~1 s del sitio.
       "vigilancia": {"delays": (1.2, 2.6, 5.0, 10.0, 30.0), "real": 1.2, "stop": 0.10, "trails": (0.03, 0.05)},
       "listados_upbit": {"delays": (1.0, 2.0, 3.0, 5.0, 10.0), "real": 1.0, "stop": 0.10, "trails": (0.05, 0.10)}}
C = CFG[HYP]
CAPS = (0.0025, 0.005, 0.01, 0.02, 0.05)
BEFORE_S, AFTER_S = 60, 3900


def get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fmt(ms: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ms / 1000))


# -- eventos ---------------------------------------------------------------------------------------------------------
def cms(catalog: int, since_ms: int) -> list[dict]:
    f = D / f"cms-{catalog}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    arts = []
    for page in range(1, 80):
        try:
            d = get_json(f"{CMS}?type=1&catalogId={catalog}&pageNo={page}&pageSize=50")
        except urllib.error.HTTPError as e:
            print("el CMS contestó", e.code, "- corto acá (L-7)", flush=True)
            break
        items = (((d or {}).get("data") or {}).get("catalogs") or [{}])[0].get("articles") or []
        if not items:
            break
        arts += [{"title": a.get("title", ""), "t0": int(a.get("releaseDate") or 0)} for a in items]
        print(f"catálogo {catalog}, página {page}: {len(arts)} avisos, hasta {fmt(arts[-1]['t0'])[:10]}", flush=True)
        if arts[-1]["t0"] < since_ms:
            break
        time.sleep(30.0)                              # L-7
    arts = [a for a in arts if a["t0"] >= since_ms]
    f.write_text(json.dumps(arts, ensure_ascii=False), encoding="utf-8")
    return arts


COIN = re.compile(r"^\(?([A-Z0-9]{2,15})\)?$")


def coins_list(text: str) -> list[str]:
    out = []
    for part in re.split(r",|\band\b|&", text):
        m = COIN.match(part.strip())
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def events_deslistados() -> list[dict]:
    out = []
    for a in cms(161, 1735689600000):                 # desde el 1-ene-2025
        t = a["title"].strip()
        if m := re.match(r"^Binance Will Delist (?P<c>.+?)(?: on \d{4}-\d{2}-\d{2}.*)?$", t):
            kind, cs = "deslistado", coins_list(m.group("c"))
        elif m := re.match(r"^Binance Will Extend the Monitoring Tag to (?:Include )?(?P<c>.+?)(?: on \d{4}-\d{2}-\d{2}.*)?$", t):
            kind, cs = "vigilancia", coins_list(m.group("c"))
        elif re.search(r"Futures Will Delist", t, re.I):
            kind, cs = "deslistado_perpetuo", list(dict.fromkeys(re.findall(r"\b([A-Z0-9]{2,15})USDT\b", t)))
        elif re.search(r"remov|cease", t, re.I):
            kind, cs = "quita_pares_o_cese", list(dict.fromkeys(re.findall(r"\(([A-Z0-9]{2,15})\)", t)))
        else:
            continue
        out += [{"t0": a["t0"], "coin": c, "side": -1, "kind": kind, "title": t} for c in cs]
    return out


def events_launchpool() -> list[dict]:
    out, seen = [], set()
    for a in sorted(cms(48, 1704067200000), key=lambda a: a["t0"]):      # desde el 1-ene-2024
        t = a["title"]
        kind = ("launchpool" if re.search(r"launchpool", t, re.I) else "megadrop" if re.search(r"megadrop", t, re.I)
                else "hodler" if re.search(r"hodler airdrop", t, re.I) else None)
        if kind is None:
            continue
        key = (kind, tuple(re.findall(r"\(([A-Z0-9]{2,15})\)", t)) or t)
        if key in seen:                               # cada proyecto, su primer aviso
            continue
        seen.add(key)
        out.append({"t0": a["t0"], "coin": "BNB", "side": 1, "kind": kind, "title": t})
    return out


UPBIT_KINDS = (("levantada", +1, re.compile(r"지정\s*해제")),
               ("extendida", -1, re.compile(r"지정\s*(?:기간\s*)?연장")),        # Upbit "지정 기간 연장", Bithumb "지정 연장"
               ("fin_del_soporte", -1, re.compile(r"거래지원\s*종료")),
               ("advertencia", -1, re.compile(r"유의\s*종목\s*지정(?!\s*기간)|유의\s*촉구")))


def upbit_notices() -> list[dict]:
    f = D / "upbit-avisos.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    out = []
    for page in range(1, 80):
        try:
            d = get_json(UPBIT.format(p=page))
        except urllib.error.HTTPError as e:
            print("Upbit contestó", e.code, "- corto acá", flush=True)
            break
        notes = ((d or {}).get("data") or {}).get("notices") or []
        if not notes:
            break
        for n in notes:
            when = n.get("first_listed_at") or n.get("listed_at")
            try:
                t = calendar.timegm(time.strptime(when[:19], "%Y-%m-%dT%H:%M:%S")) - 9 * 3600
            except (TypeError, ValueError):
                continue
            out.append({"t0": t * 1000, "title": n.get("title", "")})
        print(f"Upbit página {page}: {len(out)} avisos, hasta {fmt(out[-1]['t0'])[:10]}", flush=True)
        if out[-1]["t0"] < 1735689600000:            # desde 2025
            break
        time.sleep(1.0)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def korean(exchange: str, t0: int, title: str) -> list[dict]:
    """Un título de Upbit o Bithumb → (moneda, tipo, lado) por cada moneda. Los títulos mezclados ("A 지정 연장, B
    거래지원 종료") se parten y cada tramo toma el tipo del que lo sigue. Un cambio de fecha (변경) no cuenta (7-nov: los tres
    de la muestra no tenían perpetuo; las mismas reglas que decision/korean_notices.py)."""
    out, kind = [], None
    for seg in reversed(re.split(r",\s*|\s+및\s+", title)):
        if "변경" in seg:                              # un cambio de fecha de un aviso anterior: no es nuevo
            kind = None
            continue
        for name, side, rx in UPBIT_KINDS:
            if rx.search(seg) or (name == "advertencia" and re.search(r"거래유의종목\s*지정(?!\s*(?:연장|해제))", seg)):
                kind = (name, side)
                break
        else:
            if re.search(r"지정\s*연장", seg):
                kind = ("extendida", -1)
        for c in re.findall(r"\(([A-Z0-9]{1,15})\)", seg):
            if kind and c not in ("KRW", "BTC", "USDT"):
                out.append({"t0": t0, "coin": c, "side": kind[1], "kind": f"{exchange}_{kind[0]}", "title": title})
    return out


def events_advertencias() -> list[dict]:
    out = []
    for n in upbit_notices():
        out += korean("upbit", n["t0"], n["title"])
    raw = json.loads((D / "bithumb-avisos.json").read_text(encoding="utf-8"))
    seen = set()
    for _id, kst, title in [*raw.get("5", []), *raw.get("6", [])]:
        if _id in seen or "일정 안내" in title:
            continue
        seen.add(_id)
        t0 = (calendar.timegm(time.strptime(kst, "%Y-%m-%d %H:%M:%S")) - 9 * 3600) * 1000
        out += korean("bithumb", t0, title)
    return [e for e in out if e["t0"] >= 1735689600000]


VIGILANCIA = re.compile(r"Monitoring Tag (?:to Include|to|on) (.*?)(?:\bon\b \d{4}-\d{2}-\d{2}|$)", re.I)
TOKEN = re.compile(r"\b[A-Z0-9]{2,12}\b")
SKIP = {"BINANCE", "USDT", "USD", "USDC", "FDUSD", "TRY", "EUR", "BTC", "ETH", "BNB", "AND", "THE", "ON", "WILL",
        "DELIST", "SPOT", "MARGIN", "FUTURES", "PERPETUAL", "CONTRACTS", "UTC", "SOME", "PAIRS", "TRADING"}


def events_vigilancia() -> list[dict]:
    """Etiqueta de vigilancia de Binance (7-nov, la hipótesis del dueño: "sangría lenta pero segura", corto saliendo a
    la hora): casi todas salen en "Latest Binance News" (49) y alguna en "Delisting" (161). Las mismas reglas de título
    que el estudio del 5-nov (estudios/vigilancia_binance.py); las que la sacan ("Remove"), afuera."""
    out, seen = [], set()
    for cat in (49, 161):
        for a in cms(cat, 1735689600000):
            m = VIGILANCIA.search(a["title"])
            if not m or re.search(r"remov", a["title"], re.I):
                continue
            for c in TOKEN.findall(m.group(1)):
                if c not in SKIP and (a["t0"], c) not in seen:
                    seen.add((a["t0"], c))
                    out.append({"t0": a["t0"], "coin": c, "side": -1, "kind": "vigilancia", "title": a["title"]})
    return out


def events_listados_upbit() -> list[dict]:
    """Listados de Upbit desde 2025 (7-nov, la hipótesis del dueño: ¿el alta en wones de una moneda que ya estaba en
    BTC o USDT tiene una ventana más limpia que un listado nuevo?): el listado nuevo (신규 거래지원), el alta en wones
    (KRW 마켓 디지털 자산 추가) y, para comparar, el alta en BTC o USDT. Largo."""
    out = []
    for n in upbit_notices():
        t = n["title"]
        if re.search(r"신규\s*거래지원", t):
            kind = "listado_nuevo"
        elif re.search(r"KRW[^)]*마켓\s*디지털\s*자산\s*추가", t):
            kind = "alta_en_wones"
        elif re.search(r"(?:BTC|USDT)[^)]*마켓\s*디지털\s*자산\s*추가", t):
            kind = "alta_en_btc_o_usdt"
        else:
            continue
        for c in dict.fromkeys(re.findall(r"\(([A-Z0-9]{1,15})\)", t)):
            if c not in ("KRW", "BTC", "USDT"):
                out.append({"t0": n["t0"], "coin": c, "side": 1, "kind": kind, "title": t})
    return [e for e in out if e["t0"] >= 1735689600000]


# -- precios ---------------------------------------------------------------------------------------------------------
def _zip_rows(url: str, tmp: Path):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=300) as r, tmp.open("wb") as fh:
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
    return tmp


def tape(symbol: str, t0: int) -> list | None:
    """(ms, precio, cantidad) de cada operación del perpetuo de 1 min antes a 65 min después."""
    f = CACHE / f"{symbol}-{t0}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    t_from, t_to = t0 - BEFORE_S * 1000, t0 + AFTER_S * 1000
    out = []
    for day in sorted({time.strftime("%Y-%m-%d", time.gmtime(x / 1000)) for x in (t_from, t_to)}):
        tmp = _zip_rows(AGG.format(s=symbol, d=day), CACHE / f"_{symbol}-{day}.zip")
        if tmp is None:
            return None
        try:
            with zipfile.ZipFile(tmp) as z, z.open(z.namelist()[0]) as fh:
                for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                    if not row or not row[0].isdigit():
                        continue
                    t = int(row[5])
                    if t > t_to:
                        break
                    if t >= t_from:
                        out.append((t, float(row[1]), float(row[2])))
        finally:
            tmp.unlink(missing_ok=True)
    out.sort()
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def klines_1m(symbol: str, t0: int) -> list:
    """Velas de 1 min del perpetuo (ms, apertura, máximo, mínimo, cierre) de t0 a t0 + 4 h 5 min."""
    f = CACHE / f"k1m-{symbol}-{t0}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    t_to, out = t0 + (14400 + 300) * 1000, []
    for day in sorted({time.strftime("%Y-%m-%d", time.gmtime(x / 1000)) for x in (t0, t_to)}):
        tmp = _zip_rows(K1M.format(s=symbol, d=day), CACHE / f"_k-{symbol}-{day}.zip")
        if tmp is None:
            continue
        try:
            with zipfile.ZipFile(tmp) as z, z.open(z.namelist()[0]) as fh:
                for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                    if row and row[0].isdigit() and t0 - 60_000 <= int(row[0]) <= t_to:
                        out.append((int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        finally:
            tmp.unlink(missing_ok=True)
    out.sort()
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def bnb(t0: int) -> tuple[list, list]:
    """BNB: velas de 1 s del spot (1 min antes a 15,6 min después) como operaciones (apertura, máximo, mínimo y cierre
    repartidos en el segundo) y velas de 1 min hasta las 4 h."""
    f = CACHE / f"bnb-{t0}.json"
    if f.exists():
        got = json.loads(f.read_text(encoding="utf-8"))
        return got["tape"], got["k1m"]
    base = "https://api.binance.com/api/v3/klines?symbol=BNBUSDT"
    s1 = get_json(f"{base}&interval=1s&startTime={t0 - 60_000}&limit=1000")
    time.sleep(0.3)
    m1 = get_json(f"{base}&interval=1m&startTime={t0 - 60_000}&limit=250")
    time.sleep(0.3)
    tp = []
    for k in s1:
        t, o, h, lo, c, v = int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])
        up = c >= o                                   # el orden dentro del segundo: el camino más corto
        mids = (h, lo) if up else (lo, h)
        tp += [(t, o, v / 4), (t + 250, mids[0], v / 4), (t + 500, mids[1], v / 4), (t + 750, c, v / 4)]
    k1 = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in m1]
    f.write_text(json.dumps({"tape": tp, "k1m": k1}), encoding="utf-8")
    return tp, k1


# -- análisis --------------------------------------------------------------------------------------------------------
class Tape:
    def __init__(self, rows, k1m):
        self.t = [r[0] for r in rows]
        self.p = [r[1] for r in rows]
        self.q = [r[2] for r in rows]
        self.k1m = k1m
        self.end = self.t[-1] if self.t else 0

    def first_at(self, t):
        i = bisect.bisect_left(self.t, t)
        return i if i < len(self.t) else None

    def last_before(self, t):
        i = bisect.bisect_left(self.t, t) - 1
        return i if i >= 0 else None

    def run(self, t_in, side, hold_s, stop, trail=None):
        """Retorno neto entrando en la primera operación desde t_in: stop fijo, stop que sigue al pico (solo dentro de
        los 65 min con cada operación) o salida a los hold_s (más allá de los 65 min, con las velas de 1 min)."""
        i = self.first_at(t_in)
        if i is None:
            return None
        px, t_e = self.p[i], self.t[i]
        end, best, last = t_e + hold_s * 1000, px, None
        j = i + 1
        while j < len(self.t) and self.t[j] <= end:
            p = self.p[j]
            if (p <= px * (1 - stop)) if side > 0 else (p >= px * (1 + stop)):
                return side * (p / px - 1) - COST
            if trail:
                best = max(best, p) if side > 0 else min(best, p)
                if (p <= best * (1 - trail)) if side > 0 else (p >= best * (1 + trail)):
                    return side * (p / px - 1) - COST
            last = p
            j += 1
        if end <= self.end or trail:
            return None if last is None else side * (last / px - 1) - COST
        for ot, _o, h, lo, c in self.k1m:                 # después de los 65 min: velas de 1 min
            if ot < self.end or ot + 60_000 > end:
                continue
            if (lo <= px * (1 - stop)) if side > 0 else (h >= px * (1 + stop)):
                return -stop - COST - 0.001                   # el stop, con 0,1 % de deslizamiento
            last = c
        return None if last is None else side * (last / px - 1) - COST

    def best(self, t_from, side, horizon_s):
        """(segundos desde t_from hasta el mejor precio, retorno a favor ahí) con el cierre de cada segundo; más allá
        de los 65 min, con los máximos o mínimos de las velas de 1 min."""
        i = self.first_at(t_from)
        if i is None:
            return None
        px, closes = self.p[i], {}
        end = self.t[i] + horizon_s * 1000
        for j in range(i, len(self.t)):
            if self.t[j] > end:
                break
            closes[self.t[j] // 1000] = self.p[j]
        cand = [(sec * 1000, side * (p / px - 1)) for sec, p in closes.items()]
        if end > self.end:
            cand += [(ot, side * ((h if side > 0 else lo) / px - 1)) for ot, _o, h, lo, _c in self.k1m
                     if self.end <= ot <= end]
        if not cand:
            return None
        t, r = max(cand, key=lambda x: x[1])
        return (t - t_from) / 1000, r

    def worst(self, t_from, side, horizon_s):
        """(segundos hasta el peor precio en contra, retorno ahí) con cada operación hasta los 65 min y las velas de
        1 min después: lo que hay que aguantar (y dónde tendría que estar el stop)."""
        i = self.first_at(t_from)
        if i is None:
            return None
        px, end = self.p[i], self.t[i] + horizon_s * 1000
        cand = [(self.t[j], side * (self.p[j] / px - 1)) for j in range(i, len(self.t)) if self.t[j] <= end]
        if end > self.end:
            cand += [(ot, side * ((lo if side > 0 else h) / px - 1)) for ot, _o, h, lo, _c in self.k1m
                     if self.end <= ot <= end]
        if not cand:
            return None
        t, r = min(cand, key=lambda x: x[1])
        return (t - t_from) / 1000, r

    def sweep(self, t, side, ms=200):
        """Lo que barre una orden a mercado: el peor precio en los `ms` siguientes contra la primera operación."""
        i = self.first_at(t)
        if i is None:
            return None
        ps = [self.p[j] for j in range(i, len(self.t)) if self.t[j] <= self.t[i] + ms]
        worst = min(ps) if side < 0 else max(ps)
        return abs(worst / self.p[i] - 1)

    def ioc(self, t0, delay, side, cap):
        """¿Se llena una límite IOC con tope `cap` sobre el último precio leído 25 ms antes de mandarla?"""
        ref = self.last_before(t0 + (delay - 0.025) * 1000)
        if ref is None:
            return False
        limit = self.p[ref] * (1 - cap) if side < 0 else self.p[ref] * (1 + cap)
        t_o = t0 + delay * 1000
        i = self.first_at(t_o)
        if i is None:
            return False
        for j in range(i, len(self.t)):
            if self.t[j] > t_o + 20:
                return j == i and ((self.p[j] >= limit) if side < 0 else (self.p[j] <= limit))
            if (self.p[j] >= limit) if side < 0 else (self.p[j] <= limit):
                return True
        return False

    def volume(self, t0, secs=60):
        a, b = bisect.bisect_left(self.t, t0), bisect.bisect_left(self.t, t0 + secs * 1000)
        return sum(self.p[j] * self.q[j] for j in range(a, b))


def numbers(tp: Tape, ev: dict) -> dict:
    t0, side, stop = ev["t0"], ev["side"], C["stop"]
    pre = tp.last_before(t0)
    pre_px = tp.p[pre]
    mv = {s: (tp.p[i] / pre_px - 1 if (i := tp.first_at(t0 + s * 1000)) is not None else None)
          for s in (0.1, 1, 2, 3, 5, 10, 30, 60, 300, 900)}
    ret = {}
    for d in C["delays"]:
        for h in EXITS_S:
            ret[(d, h)] = tp.run(t0 + d * 1000, side, h, stop)
        for tr in C["trails"]:
            ret[(d, f"sigue {tr * 100:g} %")] = tp.run(t0 + d * 1000, side, 3600, stop, tr)
    real = C["real"]
    t_in = t0 + real * 1000
    return {"mv": mv, "ret": ret,
            "pico": {h: tp.best(t_in, side, h) for h in (900, 3600, 14400)},
            "piso": {h: tp.worst(t_in, side, h) for h in (900, 3600)},
            "barrido": tp.sweep(t_in, side), "ioc": {c: tp.ioc(t0, real, side, c) for c in CAPS},
            "vol_1m": tp.volume(t0), "antes": mv.get(2)}


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.2f}%"


def summary(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return f"n {len(xs)}" + (f" ({', '.join(pct(x) for x in xs)})" if xs else "")
    return (f"n {len(xs):3d} · mediana {pct(statistics.median(xs))} · media {pct(statistics.mean(xs))} · "
            f"ganan {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}%")


def label(h) -> str:
    return h if isinstance(h, str) else (f"{h // 3600} h" if h >= 3600 else f"{h // 60} min" if h >= 120 else f"{h} s")


def report(name: str, rows: list) -> None:
    if not rows:
        print(f"\n== {name}: sin eventos con datos ==")
        return
    print(f"\n== {name} (n {len(rows)}; del {fmt(rows[0]['t0'])[:10]} al {fmt(rows[-1]['t0'])[:10]}) ==")
    print("  cuánto ya se movió (a favor = +):", " · ".join(
        f"{s} s {pct(statistics.median([r['side'] * r['mv'][s] for r in rows if r['mv'][s] is not None]))}"
        for s in (0.1, 1, 2, 3, 5, 10, 60, 300, 900)))
    print("  1. rendimiento neto (mediana · media · % que ganan), por demora de entrada y salida:")
    keys = [*EXITS_S, *[f"sigue {tr * 100:g} %" for tr in C["trails"]]]
    for d in C["delays"]:
        print(f"    entrando a {d:>3} s:")
        for h in keys:
            print(f"      salida {label(h):>11}: {summary([r['ret'][(d, h)] for r in rows])}")
    print(f"  2. el pico a favor desde la entrada a {C['real']} s (mediana del momento y del tamaño):")
    for h in (900, 3600, 14400):
        got = [r["pico"][h] for r in rows if r["pico"][h] is not None]
        if got:
            ts = sorted(x[0] for x in got)
            print(f"    en {label(h):>6}: llega a los {statistics.median(ts):6.0f} s (cuartos {ts[len(ts) // 4]:.0f} y "
                  f"{ts[3 * len(ts) // 4]:.0f} s) · tamaño {pct(statistics.median([x[1] for x in got]))}")
    buckets = ((0, 10), (10, 60), (60, 300), (300, 900), (900, 3600), (3600, 14401))
    got = [r["pico"][14400][0] for r in rows if r["pico"][14400] is not None]
    print("    cuándo (4 h):", " · ".join(f"{a}-{b} s: {sum(a <= x < b for x in got)}" for a, b in buckets))
    for h in (900, 3600):                             # lo peor en contra: lo que hay que aguantar
        got = [r["piso"][h] for r in rows if (r.get("piso") or {}).get(h) is not None]
        if got:
            ts = sorted(x[0] for x in got)
            print(f"    lo peor en contra en {label(h):>6}: a los {statistics.median(ts):6.0f} s · tamaño mediano "
                  f"{pct(statistics.median([x[1] for x in got]))} · el peor {pct(min(x[1] for x in got))}")
    print(f"  resumen (entrando a {C['real']} s): " + " · ".join(
        f"{label(h)} {summary([r['ret'][(C['real'], h)] for r in rows])}" for h in (60, 300, 3600)))
    real = C["real"]
    cands = [(h, [r["ret"][(real, h)] for r in rows]) for h in keys]
    cands = [(h, [x for x in xs if x is not None]) for h, xs in cands]
    cands = [(h, xs) for h, xs in cands if len(xs) >= 3]
    if cands:
        best = max(cands, key=lambda c: statistics.mean(c[1]))
        half = len(rows) // 2
        a = [x for r in rows[:half] if (x := r["ret"][(real, best[0])]) is not None]
        b = [x for r in rows[half:] if (x := r["ret"][(real, best[0])]) is not None]
        print(f"  3. la mejor salida entrando a {real} s (por media): {label(best[0])} → {summary(best[1])}; "
              f"1.ª mitad {pct(statistics.mean(a)) if a else '—'}, 2.ª {pct(statistics.mean(b)) if b else '—'}")
    sw = sorted(r["barrido"] for r in rows if r["barrido"] is not None)
    vol = sorted(r["vol_1m"] for r in rows)
    print(f"  4. liquidez a {real} s: barrido de una orden a mercado en 200 ms: mediana {pct(statistics.median(sw))}, "
          f"p90 {pct(sw[int(len(sw) * 0.9)] if sw else None)} · operado en el 1.er minuto: mediana "
          f"{statistics.median(vol):,.0f} USDT (cuartos {vol[len(vol) // 4]:,.0f} y {vol[3 * len(vol) // 4]:,.0f})")
    print("     límite IOC: se llena", " · ".join(
        f"tope {c * 100:g} %: {sum(r['ioc'][c] for r in rows)} de {len(rows)}" for c in CAPS))


# -- 5. como la opera el agente (7-nov) -------------------------------------------------------------------------------
CAPS_FINE = (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)


def cached_tape(ev: dict):
    """La ventana del evento desde la caché (sin bajar nada): la misma que usó el informe."""
    for sym in (f"{ev['coin']}USDT", f"1000{ev['coin']}USDT"):
        if (CACHE / f"{sym}-{ev['t0']}.json").exists():
            rows = tape(sym, ev["t0"])
            if rows:
                tp = Tape(rows, klines_1m(sym, ev["t0"]))
                pre = tp.last_before(ev["t0"])
                if pre is not None and ev["t0"] - tp.t[pre] <= BEFORE_S * 1000:
                    return tp
    return None


def agent_view(name: str, events: list, delays: tuple, hold_s: int, stop: float, brake: float) -> None:
    """Con los frenos del agente: entra si a la demora no cayó más que `brake` desde la apertura del minuto del aviso
    (lo que mira en vivo). Por demora: lo que rinde y, por tope de la límite IOC, cuántas se llenan y lo que rinde POR
    AVISO (las que no se llenan, 0): si las que solo se llenan con más tope son las mejores, conviene ensancharlo."""
    rows = []
    for ev in sorted(events, key=lambda e: e["t0"]):
        tp = cached_tape(ev)
        if tp is None:
            continue
        i0 = tp.first_at(ev["t0"] // 60000 * 60000)
        for d in delays:
            i = tp.first_at(ev["t0"] + d * 1000)
            moved = None if i is None or i0 is None else ev["side"] * (tp.p[i] / tp.p[i0] - 1)
            if moved is not None and moved > brake:
                rows.append({"d": d, "out": True})
                continue
            rows.append({"d": d, "out": False, "ret": tp.run(ev["t0"] + d * 1000, ev["side"], hold_s, stop),
                         "fills": {c: tp.ioc(ev["t0"], d, ev["side"], c) for c in CAPS_FINE}})
    print(f"\n== {HYP} · {name}: como la opera el agente (freno de {brake * 100:g} % desde la apertura del minuto, "
          f"salida {label(hold_s)}, stop {stop * 100:g} %) ==")
    for d in delays:
        rs = [r for r in rows if r["d"] == d and not r["out"] and r["ret"] is not None]
        out = sum(1 for r in rows if r["d"] == d and r["out"])
        if not rs:
            continue
        half = len(rs) // 2
        a, b = [r["ret"] for r in rs[:half]], [r["ret"] for r in rs[half:]]
        print(f"  a {d:>4} s: {summary([r['ret'] for r in rs])} · mitades {pct(statistics.mean(a)) if a else '—'} / "
              f"{pct(statistics.mean(b)) if b else '—'} · afuera por el freno {out}")
        print("    tope IOC: " + " · ".join(
            f"{c * 100:g} % {sum(r['fills'][c] for r in rs)}/{len(rs)} "
            f"({pct(sum(r['ret'] for r in rs if r['fills'][c]) / len(rs))} por aviso)" for c in CAPS_FINE))


def first_notice(events: list) -> list:
    """El fin del soporte como lo opera el agente: el PRIMER aviso de Upbit o Bithumb por moneda y día (en los
    conjuntos, Bithumb publica primero o junto)."""
    first: dict = {}
    for e in events:
        key = (e["coin"], time.strftime("%Y-%m-%d", time.gmtime(e["t0"] / 1000)))
        if key not in first or e["t0"] < first[key]["t0"]:
            first[key] = e
    exchanges: dict = {}
    for e in events:
        key = (e["coin"], time.strftime("%Y-%m-%d", time.gmtime(e["t0"] / 1000)))
        exchanges.setdefault(key, set()).add(e["kind"].split("_")[0])
    both = sum(1 for k in first if len(exchanges[k]) == 2)
    print(f"\nfin del soporte: {len(first)} monedas-día, {both} anunciadas por los dos")
    return list(first.values())


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    events = {"deslistados": events_deslistados, "launchpool": events_launchpool,
              "advertencias": events_advertencias, "vigilancia": events_vigilancia,
              "listados_upbit": events_listados_upbit}[HYP]()
    kinds: dict[str, int] = {}
    for ev in events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    print(f"\n{HYP}: {len(events)} eventos por moneda ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))})")
    by_kind: dict[str, list] = {}
    skipped = {"sin_perpetuo_o_datos": 0}
    for ev in sorted(events, key=lambda e: e["t0"]):
        if ev["coin"] == "BNB":
            rows, k1 = bnb(ev["t0"])
        else:
            rows = k1 = None
            for sym in (f"{ev['coin']}USDT", f"1000{ev['coin']}USDT"):
                rows = tape(sym, ev["t0"])
                if rows:
                    k1 = klines_1m(sym, ev["t0"])
                    break
        if not rows:
            skipped["sin_perpetuo_o_datos"] += 1
            continue
        tp = Tape(rows, k1 or [])
        pre = tp.last_before(ev["t0"])
        if pre is None or ev["t0"] - tp.t[pre] > BEFORE_S * 1000:
            skipped["sin_perpetuo_o_datos"] += 1
            continue
        got = numbers(tp, ev)
        by_kind.setdefault(ev["kind"], []).append({**ev, **got})
        r = got["ret"]
        print(f"{fmt(ev['t0'])} {ev['kind']:24} {ev['coin']:10} 2 s {pct(ev['side'] * (got['mv'][2] or 0))} · "
              f"60 s {pct(ev['side'] * (got['mv'][60] or 0))} · a {C['real']} s→15 min "
              f"{pct(r[(C['real'], 900)])}  {ev['title'][:50]}", flush=True)
    print(f"\nafuera: {skipped}")
    for kind in sorted(by_kind):
        report(f"{HYP} · {kind}", by_kind[kind])
    if HYP == "deslistados":                          # 5. el tope de la orden con el freno de la regla (4 %)
        agent_view("deslistado", [e for e in events if e["kind"] == "deslistado"], (1.3, 2.0), 14400, 0.20, 0.04)
    elif HYP == "advertencias":                       # 5. las dos reglas de decision/korean_notices.py
        agent_view("advertencia de Upbit", [e for e in events if e["kind"] == "upbit_advertencia"],
                   (1.0, 2.0, 3.0, 5.0, 10.0, 30.0), 14400, 0.10, 0.03)
        agent_view("fin del soporte, el primer aviso", first_notice(
            [e for e in events if e["kind"].endswith("fin_del_soporte")]), (1.0, 2.0, 3.0, 5.0, 10.0), 300, 0.10, 0.06)


if __name__ == "__main__":
    main()
