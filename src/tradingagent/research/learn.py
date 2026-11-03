"""Aprendizaje con disciplina (REGLAS C-5): qué categorías de noticias tienen evidencia de mover el precio
a favor, medido con lo que de verdad se podía capturar desde la llegada (`r_recv_15m`, `r_recv_60m` del
etiquetador) y neto de comisiones (0,2 % ida y vuelta).

Solo reduce riesgo:
- apaga una categoría, por lado (largo o corto), cuando hay evidencia negativa: n ≥ 30 y el límite
  superior del IC95 de la ganancia neta media < 0, en cualquiera de los dos horizontes;
- nunca agranda nada del sobre ni prende algo que la política tenga apagado. Si la evidencia cambia, la
  categoría vuelve al valor por defecto (operable), que es lo que el dueño ya aprobó.

Además ajusta el meta-modelo (intelligence/meta.py) con las etiquetas de triple barrera del etiquetador:
para cada noticia y activo, el alfa que tenía al entrar (un minuto después de la llegada) y si tocó primero
el take-profit. κ ≤ 1: los datos pueden achicar la confianza del modelo, nunca agrandarla.

**Solo cuenta lo que se habría operado** (meta-labeling, `entry_alpha`): para las categorías y para el
meta-modelo, solo las apuestas que el modelo primario de verdad propone, con los umbrales de la decisión: la
noticia habla de ese activo (relevancia C ≥ `c_min`) y el alfa al entrar todavía alcanza (|α| ≥ `alpha_min`).
El 3-nov, 46 de las 47 muestras del meta-modelo eran noticias que nunca se iban a operar (37 con BTC "de
referencia" de noticias ajenas como Ferrari, y alfas casi cero): con expiración casi nunca tocan el
take-profit, κ quedó en 0, p = 1/3 en todo y se frenó toda entrada, listados incluidos.

Los resultados quedan en `aprendizaje/estado.json` de la carpeta de datos, con la fecha y la versión del
clasificador, y se recalculan como mucho una vez por hora.
"""
import json
import logging
import math
from pathlib import Path
from statistics import NormalDist, fmean, stdev

from .. import paths
from ..decision.policy import PolicyConfig
from ..intelligence import meta, vector
from ..intelligence.classifier import NOT_TRADABLE, Signal, classify
from ..intelligence.classifier import VERSION as NLP_VERSION

log = logging.getLogger("tradingagent.learn")
DIR, STATE = "aprendizaje", "estado.json"
COST = 0.002                       # 0,1 % por lado: comisión estándar de Binance spot, ida y vuelta
MIN_N = 30
HORIZONS = ("r_recv_15m", "r_recv_60m")
EVERY_S = 3600
SIDES = {1: "largo", -1: "corto"}
_POLICY = PolicyConfig()           # los mismos umbrales con los que se decide entrar (C-6)
C_MIN, ALPHA_MIN = _POLICY.c_min, _POLICY.alpha_min


def t_quantile(p: float, dof: int) -> float:
    """Cuantil de la t de Student (Cornish-Fisher; error < 0,1 % desde 10 grados de libertad)."""
    z = NormalDist().inv_cdf(p)
    if dof <= 0:
        return math.inf
    return (z + (z ** 3 + z) / (4 * dof) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * dof ** 2)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * dof ** 3))


def summarize(xs: list[float]) -> dict:
    """n, media, desvío e IC95 (t de Student) de una muestra de rendimientos netos."""
    n = len(xs)
    if n < 2:
        return {"n": n, "media": round(xs[0], 6) if xs else None, "desvio": None, "ic95": None}
    m, s = fmean(xs), stdev(xs)
    half = t_quantile(0.975, n - 1) * s / math.sqrt(n)
    return {"n": n, "media": round(m, 6), "desvio": round(s, 6), "ic95": [round(m - half, 6), round(m + half, 6)]}


def _rows(data: Path, kind: str) -> list[dict]:
    out = []
    for f in sorted((data / kind).glob("*.jsonl")) if (data / kind).exists() else ():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def signal_of(event: dict) -> Signal:
    """La clasificación guardada si es de esta versión del clasificador; si no, se recalcula (determinista)."""
    nlp = event.get("nlp") or {}
    if nlp.get("version") == NLP_VERSION:
        try:
            return Signal(nlp["category"], float(nlp["polarity"]), float(nlp.get("confidence", 0.5)),
                          tuple(nlp["assets"]), int(nlp.get("horizon_min", 60)), tuple(nlp.get("rules", ())),
                          certainty=float(nlp.get("certainty", 1.0)))
        except (KeyError, TypeError, ValueError):
            pass
    return classify(event["title"], event.get("snippet", ""), tier=event.get("tier", 2),
                    cluster_sources=event.get("cluster_sources", 1))


def entry_alpha(sig: Signal, asset: str, event: dict) -> float | None:
    """El alfa con el que se habría entrado (un minuto después de la llegada), o None si el modelo primario no
    habría propuesto la apuesta: la noticia no habla de ese activo (C < c_min) o el alfa ya era chico."""
    v = vector.vector(sig, asset, event)
    dt, _ = vector.age_s(event, float(event["received_at"]) + 60)
    alpha = vector.decay(v.alpha, dt)
    return alpha if v.C >= C_MIN and abs(alpha) >= ALPHA_MIN else None


def samples(events: list[dict], reactions: list[dict], cost: float = COST) -> dict[str, dict[str, list[float]]]:
    """{"categoría:lado": {horizonte: [ganancia neta de operar la noticia desde la llegada]}}, solo con lo que
    se habría operado (un listado que llegó a los 5 minutos no dice nada de los que llegan a tiempo)."""
    by_event: dict[str, list[dict]] = {}
    for r in reactions:
        if "error" not in r:
            by_event.setdefault(r["event_id"], []).append(r)
    out: dict[str, dict[str, list[float]]] = {}
    for e in events:
        if e.get("dedup") != "new" or e.get("is_noise") or e["id"] not in by_event or "received_at" not in e:
            continue
        sig = signal_of(e)
        category, polarity = sig.category, sig.polarity
        if category in NOT_TRADABLE or not polarity:
            continue
        sign = 1 if polarity > 0 else -1
        symbols = {f"{a}USDT" for a in sig.assets}
        for r in by_event[e["id"]]:
            if r["symbol"] not in symbols or entry_alpha(sig, r["symbol"].removesuffix("USDT"), e) is None:
                continue
            for h in HORIZONS:
                if r.get(h) is not None:
                    out.setdefault(f"{category}:{SIDES[sign]}", {}).setdefault(h, []).append(sign * r[h] - cost)
    return out


def meta_samples(events: list[dict], reactions: list[dict]) -> dict[str, list[tuple[float, int]]]:
    """{"categoría:lado": [(p del prior con el alfa al entrar, 1 si tocó primero el take-profit)]}."""
    by_event: dict[str, list[dict]] = {}
    for r in reactions:
        if "error" not in r:
            by_event.setdefault(r["event_id"], []).append(r)
    out: dict[str, list[tuple[float, int]]] = {}
    for e in events:
        if e.get("dedup") != "new" or e.get("is_noise") or e["id"] not in by_event or "received_at" not in e:
            continue
        sig = signal_of(e)
        if sig.category in NOT_TRADABLE or not sig.polarity:
            continue
        for r in by_event[e["id"]]:
            asset = r["symbol"].removesuffix("USDT")
            if asset not in sig.assets:
                continue
            alpha = entry_alpha(sig, asset, e)             # se entra al minuto siguiente a la llegada
            if alpha is None:                              # el modelo primario no la habría propuesto
                continue
            side = SIDES.get((alpha > 0) - (alpha < 0))
            if side is None or r.get(f"tb_{side}") is None:
                continue
            out.setdefault(f"{sig.category}:{side}", []).append((meta.prior(alpha), int(r[f"tb_{side}"] == 1)))
    return out


def evaluate(events: list[dict], reactions: list[dict], *, min_n: int = MIN_N, cost: float = COST) -> dict:
    stats, off = {}, []
    for key, by_h in sorted(samples(events, reactions, cost).items()):
        stats[key] = {h: summarize(xs) for h, xs in sorted(by_h.items())}
        bad = [h for h, s in stats[key].items() if s["n"] >= min_n and s["ic95"] and s["ic95"][1] < 0]
        if bad:
            off.append(key)
            stats[key]["apagada_por"] = bad
    return {"categorias": stats, "apagadas": off, "costo": cost, "min_n": min_n, "nlp": NLP_VERSION,
            "meta": meta.fit(meta_samples(events, reactions), min_n)}


def update(data_dir, now: float, *, every_s: float = EVERY_S, force: bool = False) -> tuple[dict, list[str]]:
    """(resultado vigente, categorías recién apagadas). Recalcula como mucho cada `every_s` segundos."""
    data = Path(data_dir)
    path = data / DIR / STATE
    prev = paths.read_json(path, None) or {}
    if not force and prev and now - prev.get("calculado", 0) < every_s and prev.get("nlp") == NLP_VERSION:
        return prev, []
    res = evaluate(_rows(data, "eventos"), _rows(data, "reacciones"))
    res["calculado"] = round(now)
    paths.write_json_atomic(path, res)
    newly = sorted(set(res["apagadas"]) - set(prev.get("apagadas", [])))
    if newly:
        log.warning("aprendizaje: categorías apagadas por evidencia negativa: %s", ", ".join(newly))
    return res, newly


def describe_es(res: dict, key: str) -> str:
    """La categoría apagada, en una frase para el dueño (Telegram)."""
    from ..alerts.model import category_es, side_es
    cat, _, side = key.partition(":")
    st = res["categorias"].get(key, {})
    n = max((st[h]["n"] for h in st.get("apagada_por", []) if h in st), default=0)
    return (f"las noticias de {category_es(cat)}, {side_es(side)}, vienen saliendo mal ({n} casos, contando "
            "comisiones).")


def describe(res: dict, key: str) -> str:
    """Texto corto con la evidencia de una categoría apagada (para Telegram)."""
    st = res["categorias"].get(key, {})
    parts = []
    for h in st.get("apagada_por", []):
        s = st[h]
        parts.append(f"{h.removeprefix('r_recv_')}: n={s['n']}, media {s['media'] * 100:+.2f} %, IC95 "
                     f"[{s['ic95'][0] * 100:+.2f}, {s['ic95'][1] * 100:+.2f}] %")
    return f"{key} ({'; '.join(parts)})"
