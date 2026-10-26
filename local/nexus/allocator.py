"""Asignador de pares del laboratorio: explorar y explotar, con prueba fuera de muestra.

Antes el laboratorio rotaba pares cada 6 h por filtros fijos (spread y volumen) y podía soltar un
par que venía ganando. Ahora, al re-elegir, se califica cada par con sus últimas vueltas del modelo
de llenado y la versión de estrategia vigentes:

    puntaje = media − desvío / √n     (cota inferior: castiga la suerte de pocas vueltas y los stops)

Los pares con puntaje positivo quedan PROMOVIDOS y se conservan (explotar); los lugares libres se
llenan con candidatos nuevos (explorar). Las reglas (ventana, mínimo de vueltas, días, cota) están
fijadas de antemano y no se ajustan mirando resultados.

Prueba honesta: cada vuelta lleva, desde que ENTRA, si su par estaba "promovido" o "explorando".
Como la promoción se decidió solo con vueltas anteriores, el resultado de las vueltas "promovido"
es fuera de muestra. Si no supera a "explorando", elegir por evidencia no sirve y el informe lo
muestra (no se pasa nada a dinero real por esto: es información para decidir).
"""
import math
from collections import defaultdict

PROMOTED, EXPLORING = "promovido", "explorando"


def score(pnls, min_n):
    """Cota inferior de la ganancia media por vuelta, o None si hay menos de min_n vueltas."""
    n = len(pnls)
    if n < max(min_n, 2):
        return None
    mean = sum(pnls) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1))
    return round(mean - sd / math.sqrt(n), 5)


def scores(trades, strategy, now, window=60, min_n=20, days=7):
    """{par: {"score", "n", "mean"}} con las últimas `window` vueltas de laboratorio de los últimos
    `days` días, de la versión vigente (el grupo de control no cuenta: es otra estrategia)."""
    by = defaultdict(list)
    for t in sorted(trades, key=lambda t: t["ts"]):
        if t.get("lab") and t.get("strategy") == strategy and t["ts"] >= now - days * 86400:
            by[t["sym"]].append(t["pnl"])
    out = {}
    for sym, pnls in by.items():
        last = pnls[-window:]
        out[sym] = {"score": score(last, min_n), "n": len(last), "mean": round(sum(last) / len(last), 5)}
    return out


def decide(state, sc, now, keep_max=6, exclude=(), eval_max=3, eval_min_n=5):
    """Actualiza las promociones con los puntajes nuevos y devuelve (estado, pares a conservar).
    Un par se promueve con puntaje > 0 y deja de estarlo cuando su puntaje ya no es positivo o se
    quedó sin vueltas recientes. Los excluidos (pausados, operados) siguen calificándose pero no
    ocupan lugar en el laboratorio.
    También se conservan hasta `eval_max` pares EN EVALUACIÓN (media > 0 con al menos `eval_min_n`
    vueltas pero todavía sin las necesarias para la cota): sin esto, al re-elegir (o al reiniciar por
    una actualización) se soltaba un par prometedor antes de juntar evidencia suficiente."""
    promoted = dict(state.get("promoted") or {})
    history = list(state.get("history") or [])
    for sym, v in sc.items():
        good = v["score"] is not None and v["score"] > 0
        if good and sym not in promoted:
            promoted[sym] = now
            history.append({"ts": now, "sym": sym, "event": PROMOTED, "score": v["score"], "n": v["n"]})
    for sym in list(promoted):
        v = sc.get(sym) or {}
        if v.get("score") is None or v["score"] <= 0:
            del promoted[sym]
            history.append({"ts": now, "sym": sym, "event": "descartado", "score": v.get("score"), "n": v.get("n", 0)})
    keep = sorted((s for s in promoted if s not in set(exclude)), key=lambda s: -sc[s]["score"])[:keep_max]
    evaluating = sorted((s for s, v in sc.items() if s not in promoted and s not in set(exclude)
                         and v["score"] is None and v["n"] >= eval_min_n and (v.get("mean") or 0) > 0),
                        key=lambda s: -(sc[s].get("mean") or 0))[:eval_max]
    return {"ts": now, "promoted": promoted, "evaluating": evaluating, "scores": sc,
            "history": history[-200:]}, keep + evaluating


def recommend(state, top=3):
    """Pares promovidos con mejor cota inferior: candidatos para el dinero real (el dueño decide)."""
    sc = state.get("scores") or {}
    best = sorted((s for s in (state.get("promoted") or {}) if (sc.get(s) or {}).get("score")),
                  key=lambda s: -sc[s]["score"])[:top]
    return [{"sym": s} | sc[s] for s in best]


def status(state, sym):
    return PROMOTED if sym in (state.get("promoted") or {}) else EXPLORING
