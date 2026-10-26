"""Selector del dinero real: opera la combinación (par, variante) que HOY gana en el laboratorio y la
cambia sola cuando el real pierde.

Pedido del dueño (25-oct-2026): el límite de pérdida no es para frenar sino para cambiar de estrategia
por otra que en ese momento esté ganando. Reglas fijadas de antemano (no se ajustan mirando resultados):

- Candidatas: cada (par, variante) del laboratorio con el modelo de llenado vigente, sus últimas
  WINDOW vueltas de los últimos DAYS días, al menos MIN_N. Puntaje = cota inferior de la ganancia por
  vuelta (media − desvío/√n) en % del monto de la vuelta. Es candidata si el puntaje es > 0.
- Elección: con `authorize --auto N`, las N mejores (una variante por par: la mejor). Con pares fijos,
  la mejor variante de cada par autorizado; si ninguna tiene evidencia todavía, la vigente.
- Rotación: si desde que se eligió una combinación el real pierde ROTATE_LOSS_PCT % del capital, o con
  al menos CALIB_N vueltas reales pierde mientras el paper de esa misma combinación gana en el mismo
  período, se descarta BAN_H horas **el par entero** (todas sus variantes) y se elige la siguiente. Antes
  se descartaba solo la variante y el selector pasaba a otra del mismo par que en ese par operaba igual
  (26-oct: ZRO vigente → ZRO control, mismas vueltas): no era cambiar de estrategia. El cambio se hace
  sin posición abierta.
- Los límites del gestor de riesgo (por día, semana y total) siguen valiendo: son la red final si
  todo lo que se prueba pierde.
"""
import math
import time
from collections import defaultdict

WINDOW, MIN_N, DAYS = 40, 15, 3
ROTATE_LOSS_PCT, CALIB_N, BAN_H = 2.0, 10, 24
CONTROL = "control"


def key(sym, strategy):
    return f"{sym}|{strategy}"


def banned_pair(sym, banned):
    return key(sym, "*") in banned


def pct(t):
    """Ganancia de una vuelta en % de su monto (el paper usa 40 €, el real lo que haya)."""
    return t["pnl"] / (t.get("notional") or 40.0) * 100


def combo_scores(trades, now, window=WINDOW, min_n=MIN_N, days=DAYS):
    """{"PAR|variante": {"sym", "strategy", "score", "n", "mean"}} con las vueltas en paper recientes."""
    by = defaultdict(list)
    for t in sorted(trades, key=lambda t: t["ts"]):
        if t["ts"] >= now - days * 86400 and t.get("strategy"):
            by[(t["sym"], t["strategy"])].append(pct(t))
    out = {}
    for (sym, strategy), xs in by.items():
        last = xs[-window:]
        n = len(last)
        mean = sum(last) / n
        score = None
        if n >= max(min_n, 2):
            sd = math.sqrt(sum((x - mean) ** 2 for x in last) / (n - 1))
            score = round(mean - sd / math.sqrt(n), 5)
        out[key(sym, strategy)] = {"sym": sym, "strategy": strategy, "score": score, "n": n, "mean": round(mean, 5)}
    return out


def pick(scores, auth, banned, vigente, allowed):
    """{par: variante} para el dinero real según la autorización. `allowed` = variantes que el agente
    sabe operar en real (vigente, control y las de lab.variants)."""
    good = sorted((v for k, v in scores.items() if v["score"] is not None and v["score"] > 0
                   and k not in banned and not banned_pair(v["sym"], banned) and v["strategy"] in allowed),
                  key=lambda v: -v["score"])
    best = {}
    for v in good:
        best.setdefault(v["sym"], v["strategy"])
    if auth.get("auto"):
        ranked = sorted(best, key=lambda s: -scores[key(s, best[s])]["score"])
        return {s: best[s] for s in ranked[:auth["auto"]]}
    out = {}
    for s in auth.get("symbols") or []:
        if banned_pair(s, banned):
            continue
        if s in best:
            out[s] = best[s]
        elif key(s, vigente) not in banned:
            out[s] = vigente                                  # sin evidencia todavía: la vigente
    return out


def check(choice, sym, real, paper, capital, now):
    """Motivo para descartar la combinación elegida, o None. real/paper: vueltas desde que se eligió."""
    r = [t for t in real if t["sym"] == sym and t["ts"] >= choice["since"]]
    lost = sum(t["pnl"] for t in r)
    if capital and lost <= -capital * ROTATE_LOSS_PCT / 100:
        return f"el real perdió {-lost:.2f} USDT ({ROTATE_LOSS_PCT:g}% del capital) desde que se eligió"
    p = [pct(t) for t in paper if t["sym"] == sym and t.get("strategy") == choice["strategy"] and t["ts"] >= choice["since"]]
    if len(r) >= CALIB_N and p and sum(map(pct, r)) / len(r) < 0 < sum(p) / len(p):
        return f"con {len(r)} vueltas el real pierde mientras el paper de la misma combinación gana"
    return None


def update(state, scores, auth, now, real, paper, capital, vigente, allowed, blocked=()):
    """Nuevo estado del selector: vence descartes, descarta lo que perdió (o quedó bloqueado en el
    exchange, sin posición: espera la revisión del dueño sin dejar el capital parado) y elige de nuevo."""
    banned = {k: t for k, t in (state.get("banned") or {}).items() if t > now}
    history = list(state.get("history") or [])
    choices = dict(state.get("choices") or {})
    for sym, c in list(choices.items()):
        why = ("par bloqueado en el exchange: queda para revisión del dueño" if sym in blocked
               else check(c, sym, real, paper, capital, now))
        if why:
            banned[key(sym, "*")] = now + BAN_H * 3600       # el par entero: cambiar de verdad
            history.append({"ts": now, "sym": sym, "strategy": c["strategy"], "event": "descartada", "why": why})
            del choices[sym]
    picked = pick(scores, auth or {}, banned, vigente, allowed) if auth else {}
    new = {}
    for sym, strategy in picked.items():
        old = choices.get(sym)
        new[sym] = old if old and old["strategy"] == strategy else {"strategy": strategy, "since": now}
        if new[sym] is not old:
            history.append({"ts": now, "sym": sym, "strategy": strategy, "event": "elegida",
                            "score": (scores.get(key(sym, strategy)) or {}).get("score")})
    top = sorted((v for v in scores.values() if v["score"] is not None), key=lambda v: -v["score"])[:10]
    return {"ts": now, "choices": new, "banned": banned, "top": top, "history": history[-100:]}
