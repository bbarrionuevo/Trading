"""Resultados de empresas con impulso: ¿filtrar por lo que dice la empresa de su "guidance" (si sube, baja o mantiene lo
que espera para adelante) mejora la regla? La regla de hoy (REGLAS A-5 y C-4, docs/ESTUDIO-RESULTADOS.md) no lee el
texto: a los 2 min del 8-K de resultados, si el perpetuo TradFi de Binance ya se movió ≥ 0,5 %, entra en esa dirección
y sale a los 30 min. Idea: no entrar (o entrar más) según la guidance del comunicado (anexo 99.1 del 8-K).

Con los 88 resultados de `resultados-tradfi.json` (estudio de resultados: el movimiento a 1-60 min del 8-K): para cada
uno se busca en EDGAR el 8-K del ítem 2.02 aceptado a ±120 s, se baja su anexo 99 y se lee con reglas fijas si la
guidance sube, baja, se mantiene o no se dice. Operación: la dirección a los 2 min × (movimiento a 30 min − a 2 min),
0,15 % de costos.

Resultado (6-nov, los 88 con su anexo): la regla (60 operaciones) mediana +0,93 %, media +1,17 %, 63 % positivas; con
la guidance a favor del precio (10) +1,47 % de media, 80 %; en contra (9) +0,55 %, 67 %; sin decir o mantenida (41)
+1,23 %, 59 %. Las tres positivas y con muestras chicas: sin cambio.

Uso: python estudios/resultados_guia.py <carpeta con resultados-tradfi.json>
SEC_USER_AGENT sale de %LOCALAPPDATA%\\TRADING\\.env (la SEC pide nombre y correo; nunca se muestra). Guarda los anexos
en <carpeta>/sec_anexos (datos, nunca código). Hasta 5 pedidos por segundo a la SEC (permite 10).
"""
import html
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1])
CACHE = D / "sec_anexos"
CACHE.mkdir(exist_ok=True)
ENV = Path(os.environ.get("LOCALAPPDATA", "")) / "TRADING" / ".env"
COST, MIN_MOVE, MATCH_S = 0.0015, 0.005, 120
GUIDE = r"(?:guidance|outlook|forecast)"
NEAR = r"(?:\W+\w+){0,6}?\W+"
RULES = (("sube", re.compile(rf"\b(?:rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost(?:s|ed|ing)?|lift(?:s|ed|ing)?)\b"
                             rf"{NEAR}{GUIDE}|{GUIDE}{NEAR}(?:raised|increased)\b")),
         ("baja", re.compile(rf"\b(?:lower(?:s|ed|ing)?|reduc(?:e|es|ed|ing)|cut(?:s|ting)?|withdr(?:aw|aws|ew|awn|awing)|"
                             rf"suspend(?:s|ed|ing)?)\b{NEAR}{GUIDE}|{GUIDE}{NEAR}(?:lowered|reduced|withdrawn|suspended)\b")),
         ("mantiene", re.compile(rf"\b(?:reaffirm(?:s|ed|ing)?|maintain(?:s|ed|ing)?|reiterat(?:e|es|ed|ing)|"
                                 rf"confirm(?:s|ed|ing)?)\b{NEAR}{GUIDE}")))
BOILERPLATE = re.compile(r"forward-looking|safe harbor|risks and uncertainties", re.I)


def user_agent() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "SEC_USER_AGENT" and v.strip():
            return v.strip().strip('"').strip("'")
    raise SystemExit("falta SEC_USER_AGENT en el .env")


UA = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}


def get(url: str) -> bytes:
    time.sleep(0.2)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def ms(iso: str) -> int:
    """La hora de aceptación de EDGAR (UTC: el estudio de resultados la tomó de ahí) en milisegundos."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def exhibit(cik: int, acc: str) -> str | None:
    f = CACHE / f"{cik}-{acc}.txt"
    if f.exists():
        return f.read_text(encoding="utf-8")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    page = get(f"{base}/{acc}-index.htm").decode("utf-8", errors="replace")
    href = None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):     # Seq, Descripción, Documento, Tipo
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        link = re.search(r'href="([^"]+)"', cells[2]) if len(cells) >= 4 else None
        if link and re.sub(r"<[^>]+>", "", cells[3]).strip().upper().startswith("EX-99"):
            href = link.group(1).removeprefix("/ix?doc=")
            break
    if href is None or not href.lower().endswith((".htm", ".html", ".txt")):
        return None
    raw = get("https://www.sec.gov" + href).decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
    f.write_text(text, encoding="utf-8")
    return text


def guidance(text: str) -> str:
    body = " ".join(s for s in re.split(r"(?<=[.;])\s+", text) if not BOILERPLATE.search(s)).lower()
    return next((name for name, rx in RULES if rx.search(body)), "no_dice")


def stats(xs):
    if not xs:
        return "n  0"
    return (f"n {len(xs):2d} · mediana {statistics.median(xs) * 100:+6.2f}% · media {statistics.mean(xs) * 100:+6.2f}% · "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}%")


events = json.loads((D / "resultados-tradfi.json").read_text(encoding="utf-8"))["eventos"]
ciks = {v["ticker"]: int(v["cik_str"]) for v in json.loads(get("https://www.sec.gov/files/company_tickers.json")).values()}
subs, rows = {}, []
for e in events:
    cik = ciks.get(e["tk"])
    if cik is None:
        continue
    if cik not in subs:
        subs[cik] = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))["filings"]["recent"]
    rec, t0 = subs[cik], ms(e["t0"])
    acc = next((rec["accessionNumber"][i] for i, form in enumerate(rec["form"])
                if form == "8-K" and "2.02" in (rec["items"][i] or "")
                and abs(ms(rec["acceptanceDateTime"][i]) - t0) <= MATCH_S * 1000), None)
    text = exhibit(cik, acc) if acc else None
    if text:
        rows.append({**e, "guia": guidance(text)})

print(f"resultados: {len(events)} · con el 8-K y su anexo 99 en EDGAR: {len(rows)}")
print("guidance:", {g: sum(r["guia"] == g for r in rows) for g in ("sube", "baja", "mantiene", "no_dice")})
trades = []
for r in rows:
    side = 1 if r["r2"] >= MIN_MOVE else -1 if r["r2"] <= -MIN_MOVE else 0
    if side:
        favor = {"sube": side > 0, "baja": side < 0}.get(r["guia"])
        trades.append({**r, "side": side, "ret": side * (r["r30"] - r["r2"]) - COST,
                       "grupo": "otra" if favor is None else "a_favor" if favor else "en_contra"})
print(f"\nla regla de hoy (≥ 0,5 % a los 2 min, salida a los 30 min): {stats([t['ret'] for t in trades])}")
for g, label in (("a_favor", "la guidance va en la misma dirección que el precio"),
                 ("en_contra", "la guidance va en contra del precio"),
                 ("otra", "la guidance se mantiene o no se dice")):
    print(f"  {label:52} {stats([t['ret'] for t in trades if t['grupo'] == g])}")
print("\nen contra, uno por uno:")
for t in trades:
    if t["grupo"] == "en_contra":
        print(f"  {t['tk']:6} {t['t0'][:10]} guidance {t['guia']:5} · a los 2 min {t['r2'] * 100:+5.2f}% · "
              f"{'largo' if t['side'] > 0 else 'corto'} · resultado {t['ret'] * 100:+6.2f}%")
