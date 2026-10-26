"""Paper del carry de funding con cobros REALES (sin claves ni capital).

Abre posiciones simuladas (largo contado MEXC + corto perpetuo MEXC por el mismo nocional) en los
pares que el informe de viabilidad marca como 'candidato', les acredita cada cobro de funding que
MEXC liquida después de la apertura y las valúa con los precios de salida actuales (bid del
contado, ask del perpetuo). Mide hacia adelante lo que el historial sugiere, incluida la base.

python -m nexus.paper_carry ESTADO.json VIABILIDAD.json      (lo corre research-feasibility.yml)
"""
import json
import sys
import time
from pathlib import Path

SPOT_FEE, PERP_FEE = 0.0008, 0.0004          # taker por lado: contado MEXC medido, perpetuo conservador
NOTIONAL, MAX_POSITIONS = 30.0, 2            # 2 x (30 contado + 15 margen a 2x) = 90 de 100 EUR
MIN_ENTRY_BASIS = -0.003                     # no entrar si el perpetuo cotiza >0,3% bajo el contado


def mark(pos, spot_bid, perp_ask):
    """Resultado si se cerrara ahora: funding cobrado + base - comisiones de entrada y salida."""
    n = pos["notional"]
    basis = n * (spot_bid / pos["entry_spot"] - perp_ask / pos["entry_perp"])
    fees = 2 * n * (SPOT_FEE + PERP_FEE)
    return {"funding": round(pos["funding"], 4), "basis": round(basis, 4), "fees": round(fees, 4),
            "net": round(pos["funding"] + basis - fees, 4)}


def step(state, candidates, prices, history, now=None):
    """Un ciclo. candidates: [(par, veredicto)] ordenados por preferencia; prices: {par: (spot_bid,
    spot_ask, perp_bid, perp_ask)}; history(par) -> [(ts, tasa)] liquidadas."""
    now = now or time.time()
    state.setdefault("open", {})
    state.setdefault("closed", [])
    verdicts = dict(candidates)
    for sym, pos in list(state["open"].items()):
        hist = history(sym)
        for ts, rate in hist:
            if ts > pos["last_settle"]:
                pos["funding"] += pos["notional"] * rate          # el corto cobra si la tasa es positiva
                pos["settlements"] += 1
                pos["last_settle"] = ts
        recent = [r for _, r in hist[-3:]]
        if sym not in prices:
            continue
        sb, _, _, pa = prices[sym]
        pos["mark"] = mark(pos, sb, pa)
        if verdicts.get(sym) == "descartado" or (len(recent) == 3 and sum(recent) < 0):
            state["closed"].append({**pos, "sym": sym, "closed": now, "result": pos["mark"]})
            del state["open"][sym]
    for sym, v in candidates:
        if len(state["open"]) >= MAX_POSITIONS:
            break
        if v != "candidato" or sym in state["open"] or sym not in prices:
            continue
        if any(c["sym"] == sym and now - c["closed"] < 86400 for c in state["closed"]):
            continue                                              # recién cerrada: 24 h de espera
        _, sa, pb, _ = prices[sym]
        hist = history(sym)
        if pb / sa - 1 < MIN_ENTRY_BASIS or sum(r for _, r in hist[-3:]) < 0:
            continue
        last = max((t for t, _ in hist), default=now)
        state["open"][sym] = {"opened": now, "notional": NOTIONAL, "entry_spot": sa, "entry_perp": pb,
                              "funding": 0.0, "settlements": 0, "last_settle": max(last, now)}
        state["open"][sym]["mark"] = mark(state["open"][sym], prices[sym][0], prices[sym][3])
    realized = sum(c["result"]["net"] for c in state["closed"])
    unreal = sum(p.get("mark", {}).get("net", 0) for p in state["open"].values())
    state["summary"] = {"ts": now, "open": len(state["open"]), "closed": len(state["closed"]),
                        "realized_eur": round(realized, 4), "open_net_eur": round(unreal, 4),
                        "total_eur": round(realized + unreal, 4)}
    return state


def to_markdown(state):
    s = state.get("summary", {})
    md = ["## 3. Paper del carry (cobros reales desde la apertura simulada)", "",
          f"Abiertas {s.get('open', 0)} · cerradas {s.get('closed', 0)} · realizado {s.get('realized_eur', 0):+.4f} € · "
          f"abierto {s.get('open_net_eur', 0):+.4f} € · **total {s.get('total_eur', 0):+.4f} €**", "",
          "| par | abierta | días | cobros | funding € | base € | comisiones € | neto € |", "|---|---|---|---|---|---|---|---|"]
    for sym, p in state.get("open", {}).items():
        m = p.get("mark", {})
        md.append(f"| {sym} | {time.strftime('%m-%d %H:%M', time.gmtime(p['opened']))} | "
                  f"{(s.get('ts', p['opened']) - p['opened']) / 86400:.1f} | {p['settlements']} | {m.get('funding', 0):+.4f} | "
                  f"{m.get('basis', 0):+.4f} | {m.get('fees', 0):.4f} | {m.get('net', 0):+.4f} |")
    for c in state.get("closed", [])[-10:]:
        r = c["result"]
        md.append(f"| {c['sym']} (cerrada) | {time.strftime('%m-%d %H:%M', time.gmtime(c['opened']))} | "
                  f"{(c['closed'] - c['opened']) / 86400:.1f} | {c['settlements']} | {r['funding']:+.4f} | "
                  f"{r['basis']:+.4f} | {r['fees']:.4f} | {r['net']:+.4f} |")
    return "\n".join(md) + "\n"


def main(state_path, feasibility_path):
    from .discovery import funding_history as fh
    from .discovery import sources
    feas = json.loads(Path(feasibility_path).read_text(encoding="utf-8"))
    cands = [(f["pair"], f.get("verdict")) for f in feas["funding"] if f.get("exchange") == "MEXC" and "error" not in f]
    cands.sort(key=lambda c: c[1] != "candidato")
    spot, perp = sources.mexc_book_all(), fh.mexc_perp_books()
    state = json.loads(Path(state_path).read_text(encoding="utf-8")) if Path(state_path).exists() else {}
    syms = set(state.get("open", {})) | {c for c, _ in cands}
    prices = {}
    for sym in syms:
        s, p = spot.get(sym.replace("_", "")), perp.get(sym)
        if s and p:
            prices[sym] = (s[0], s[1], p[0], p[1])
    cache = {}

    def history(sym):
        if sym not in cache:
            cache[sym] = fh.mexc_history(sym, pages=1)
        return cache[sym]

    state = step(state, cands, prices, history)
    Path(state_path).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(state)
    print(md)
    return md


if __name__ == "__main__":
    main(*sys.argv[1:3])
