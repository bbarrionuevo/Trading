"""Calibración del sistema con el simulacro (pedido del dueño, 4-nov-2026: "qué tan rápido llegamos a invertir, qué
tan rápido llegan las noticias y, más importante, cuánto ganamos y cuánto acertamos").

`python -m tradingagent informe-simulacro` junta, de lo que el agente ya guarda en la PC:
- **Llegada de las noticias**, por fuente: de publicada (hora de la fuente) a recibida (diario `journal/`).
- **Reacción**: de recibida la noticia a la entrada (clasificación, IA, riesgo y orden), y de publicada a la entrada.
- **Resultado**: operaciones cerradas, acierto (ganadas / cerradas), ganancia neta (comisiones y funding incluidos), por
  categoría y por motivo de salida, y el capital del simulacro contra el inicial.
- **Ejecución** (simulacro local, `ordenes-simuladas.jsonl`): deslizamiento contra el mejor precio y demora del libro.
Solo lee archivos locales: no sale a la red.
"""
import json
import statistics
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

from .. import paths
from .shadow import STATE, _all_trades


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _stats(xs: list[float]) -> dict | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return {"n": len(xs), "mediana": round(statistics.median(xs), 2), "p90": round(xs[int(0.9 * (len(xs) - 1))], 2)}


def report(base: Path, journal_dir: Path, *, since: float | None = None) -> dict:
    base, journal_dir = Path(base), Path(journal_dir)
    news: dict[str, dict] = {}
    for f in sorted(journal_dir.glob("journal-*.jsonl")) if journal_dir.exists() else []:
        for r in _rows(f):
            d = r.get("data") or {}
            if r.get("kind") == "news" and d.get("id") and (since is None or (d.get("received_at") or 0) >= since):
                news.setdefault(d["id"], d)
    arrival = defaultdict(list)
    for d in news.values():
        if d.get("accepted") and d.get("published_at") and d.get("received_at"):
            arrival[d.get("source", "?")].append(float(d["received_at"]) - float(d["published_at"]))
    trades = [r for r in _all_trades(base) if since is None or float(r.get("ts", 0)) >= since]
    entries = [r for r in trades if r.get("tipo") == "entrada"]
    exits = [r for r in trades if r.get("tipo") == "salida"]
    reaction, total = [], []
    for e in entries:
        n = news.get(e.get("event_id"))
        if n and n.get("received_at"):
            reaction.append(float(e["ts"]) - float(n["received_at"]))
            if n.get("published_at"):
                total.append(float(e["ts"]) - float(n["published_at"]))
    pnl = [D(str(r.get("pnl_neto", "0"))) for r in exits]
    by_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "ganadas": 0, "pnl": D(0)})
    by_reason: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": D(0)})
    for r, x in zip(exits, pnl, strict=True):
        c = by_cat[r.get("category") or "?"]
        c["n"] += 1
        c["ganadas"] += int(x > 0)
        c["pnl"] += x
        m = by_reason[r.get("motivo") or "?"]
        m["n"] += 1
        m["pnl"] += x
    sims = [r for r in _rows(base / "ordenes-simuladas.jsonl") if since is None or float(r.get("ts", 0)) >= since]
    slip = []
    for r in sims:
        if r.get("tipo") == "orden" and D(str(r.get("ejecutado") or 0)) > 0 and D(str(r.get("mejor") or 0)) > 0:
            best, px = D(str(r["mejor"])), D(str(r["precio"]))
            slip.append(float((px - best) / best * 10_000 * (1 if r.get("lado") == "BUY" else -1)))
    st = paths.read_json(base / STATE, None) or {}
    won = sum(1 for x in pnl if x > 0)
    return {
        "llegada_s": {s: _stats(v) for s, v in sorted(arrival.items(), key=lambda kv: -len(kv[1]))},
        "reaccion_s": _stats(reaction), "publicada_a_entrada_s": _stats(total),
        "entradas": len(entries), "cerradas": len(exits), "ganadas": won,
        "acierto": round(won / len(exits), 3) if exits else None,
        "pnl_neto": str(sum(pnl, D(0))), "pnl_medio": str(round(sum(pnl, D(0)) / len(pnl), 6)) if pnl else None,
        "por_categoria": {k: {**v, "pnl": str(v["pnl"])} for k, v in by_cat.items()},
        "por_motivo": {k: {**v, "pnl": str(v["pnl"])} for k, v in by_reason.items()},
        "deslizamiento_bps": _stats(slip), "libro_ms": _stats([r.get("libro_ms") for r in sims if r.get("libro_ms")]),
        "stops_de_emergencia": sum(1 for r in sims if r.get("tipo") == "stop_de_emergencia"),
        "capital_inicial": st.get("initial"), "capital": st.get("equity") or st.get("cash"),
        "abiertas": sorted(st.get("positions") or {}),
    }


def render(rep: dict, cuenta: str = "simulacro") -> str:
    """El informe en castellano, para la consola."""
    def s(x, unit="s"):
        return f"mediana {x['mediana']:g} {unit} · p90 {x['p90']:g} {unit} (n={x['n']})" if x else "sin datos"

    lines = [f"Calibración de la cuenta {cuenta}"]
    if rep.get("capital_inicial"):
        ini, now = D(str(rep["capital_inicial"])), D(str(rep["capital"] or rep["capital_inicial"]))
        pct = (now - ini) / ini * 100 if ini else D(0)
        lines.append(f"  Capital: {now:.2f} USDT (empezó con {ini:.2f}: {pct:+.2f} %)")
    lines.append(f"  Operaciones: {rep['entradas']} entradas, {rep['cerradas']} cerradas, {rep['ganadas']} ganadas"
                 + (f" (acierto {rep['acierto'] * 100:.0f} %)" if rep["acierto"] is not None else ""))
    if rep["cerradas"]:
        lines.append(f"  Ganancia neta: {D(rep['pnl_neto']):+.4f} USDT ({D(rep['pnl_medio']):+.4f} por operación)")
        for cat, v in sorted(rep["por_categoria"].items(), key=lambda kv: -kv[1]["n"]):
            lines.append(f"    · {cat}: {v['n']} cerradas, {v['ganadas']} ganadas, {D(v['pnl']):+.4f} USDT")
        lines.append("  Salidas: " + ", ".join(f"{k} {v['n']} ({D(v['pnl']):+.4f})" for k, v in
                                                rep["por_motivo"].items()))
    lines.append(f"  Reacción (de recibida la noticia a la entrada): {s(rep['reaccion_s'])}")
    lines.append(f"  De publicada la noticia a la entrada: {s(rep['publicada_a_entrada_s'])}")
    if rep["deslizamiento_bps"] or rep["libro_ms"]:
        lines.append(f"  Ejecución simulada: deslizamiento {s(rep['deslizamiento_bps'], 'bps')}; libro "
                     f"{s(rep['libro_ms'], 'ms')}; stops de emergencia disparados: {rep['stops_de_emergencia']}")
    lines.append("  Llegada de las noticias (de publicada a recibida):")
    for src, v in list(rep["llegada_s"].items())[:15]:
        lines.append(f"    · {src}: {s(v)}")
    if rep["abiertas"]:
        lines.append(f"  Abiertas ahora: {', '.join(rep['abiertas'])}")
    return "\n".join(lines)
