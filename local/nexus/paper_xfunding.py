"""Paper de diferencias de funding entre exchanges, con cobros REALES (sin claves ni capital).

Abre posiciones simuladas (corto el perpetuo donde el funding es alto, largo donde es bajo) SOLO en
diferencias que ya pagaron de verdad los últimos días (historial de ambas patas, no la foto del
momento). Desde la apertura acumula cada cobro liquidado de ambos exchanges y valúa la diferencia
de precio entre las dos patas (base), con las comisiones de entrada y salida.

python -m nexus.paper_xfunding ESTADO.json RADAR.json      (lo corre research-radar.yml)
"""
import json
import sys
import time
from pathlib import Path

NOTIONAL, MAX_POSITIONS = 50.0, 3
MIN_REAL_APR, MIN_POS_DAYS = 30.0, 0.8        # lo realmente cobrado, no la tasa del momento
MAX_ENTRY_BASIS_PCT = 0.5                     # no entrar si las patas cotizan muy distinto


def candidates(cross):
    out = []
    for x in cross:
        r = x.get("realized")
        if (r and r[1] >= MIN_REAL_APR and r[2] >= MIN_POS_DAYS and x.get("short_px") and x.get("long_px")
                and abs(x["short_px"] / x["long_px"] - 1) * 100 <= MAX_ENTRY_BASIS_PCT):
            out.append(x)
    return sorted(out, key=lambda x: -x["realized"][1])


def mark(pos, short_px, long_px):
    n = pos["notional"]
    basis = n * (1 - short_px / pos["short_entry"]) + n * (long_px / pos["long_entry"] - 1)
    return {"funding": round(pos["funding"], 4), "basis": round(basis, 4), "fees": round(pos["fees"], 4),
            "net": round(pos["funding"] + basis - pos["fees"], 4)}


def step(state, cross, history, now=None):
    """cross: diferencias del radar (con realized, símbolos y precios); history(ex, sym) -> [(ts, tasa)]."""
    now = now or time.time()
    state.setdefault("open", {})
    state.setdefault("closed", [])
    live = {(x["base"], x["short"], x["long"]): x for x in cross}
    for key, p in list(state["open"].items()):
        last24 = 0.0
        for leg, sign in (("short", 1), ("long", -1)):
            for ts, r in history(p[f"{leg}_ex"], p[f"{leg}_symbol"]):
                if ts > p[f"{leg}_last"]:
                    p["funding"] += sign * p["notional"] * r      # el corto cobra la tasa; el largo la paga
                    p[f"{leg}_last"] = ts
                    p["settlements"] += 1
                if ts > p["opened"] and ts > now - 86400:
                    last24 += sign * r
        x = live.get((p["base"], p["short_ex"], p["long_ex"]))     # misma moneda y MISMOS exchanges
        prices = (x["short_px"], x["long_px"]) if x and x.get("short_px") and x.get("long_px") else \
            (p.get("last_short_px", p["short_entry"]), p.get("last_long_px", p["long_entry"]))
        p["last_short_px"], p["last_long_px"] = prices
        p["mark"] = mark(p, *prices)
        # Se cierra solo por lo que cobraron SUS patas: un día completo abierta y las últimas 24 h negativas.
        if now - p["opened"] >= 86400 and last24 < 0:
            state["closed"].append({**p, "closed": now, "result": p["mark"]})
            del state["open"][key]
    for x in candidates(cross):
        key = f"{x['base']}:{x['short']}/{x['long']}"
        if len(state["open"]) >= MAX_POSITIONS:
            break
        if key in state["open"] or any(p["base"] == x["base"] for p in state["open"].values()) or \
                any(c["base"] == x["base"] and now - c["closed"] < 86400 for c in state["closed"]):
            continue                                              # una posición por moneda (no duplicar riesgo)
        fees = 2 * NOTIONAL * x["cost_pct"] / 100                  # entrada + salida, ambas patas
        state["open"][key] = {"base": x["base"], "opened": now, "notional": NOTIONAL, "short_ex": x["short"],
                              "long_ex": x["long"], "short_symbol": x["short_symbol"], "long_symbol": x["long_symbol"],
                              "short_entry": x["short_px"], "long_entry": x["long_px"], "short_last": now,
                              "long_last": now, "funding": 0.0, "fees": fees, "settlements": 0,
                              "realized_at_open": x["realized"]}
        state["open"][key]["mark"] = mark(state["open"][key], x["short_px"], x["long_px"])
    realized = sum(c["result"]["net"] for c in state["closed"])
    unreal = sum(p.get("mark", {}).get("net", 0) for p in state["open"].values())
    state["summary"] = {"ts": now, "open": len(state["open"]), "closed": len(state["closed"]),
                        "realized_eur": round(realized, 4), "open_net_eur": round(unreal, 4),
                        "total_eur": round(realized + unreal, 4)}
    return state


def to_markdown(state):
    s = state.get("summary", {})
    md = ["## 6. Paper de diferencias de funding (cobros reales desde la apertura simulada)", "",
          f"{NOTIONAL:.0f} € por pata · abiertas {s.get('open', 0)} · cerradas {s.get('closed', 0)} · "
          f"**total {s.get('total_eur', 0):+.4f} €** (realizado {s.get('realized_eur', 0):+.4f}, abierto {s.get('open_net_eur', 0):+.4f})", "",
          "| posición | abierta | días | cobros | funding € | base € | comisiones € | neto € |", "|---|---|---|---|---|---|---|---|"]
    for k, p in state.get("open", {}).items():
        m = p.get("mark", {})
        md.append(f"| {k} | {time.strftime('%m-%d %H:%M', time.gmtime(p['opened']))} | "
                  f"{(s.get('ts', p['opened']) - p['opened']) / 86400:.1f} | {p['settlements']} | {m.get('funding', 0):+.4f} | "
                  f"{m.get('basis', 0):+.4f} | {m.get('fees', 0):.4f} | {m.get('net', 0):+.4f} |")
    for c in state.get("closed", [])[-8:]:
        r = c["result"]
        md.append(f"| {c['base']} (cerrada) | {time.strftime('%m-%d %H:%M', time.gmtime(c['opened']))} | "
                  f"{(c['closed'] - c['opened']) / 86400:.1f} | {c['settlements']} | {r['funding']:+.4f} | "
                  f"{r['basis']:+.4f} | {r['fees']:.4f} | {r['net']:+.4f} |")
    return "\n".join(md) + "\n"


def main(state_path, radar_path):
    from .discovery import funding_multi as fm
    radar = json.loads(Path(radar_path).read_text(encoding="utf-8"))
    state = json.loads(Path(state_path).read_text(encoding="utf-8")) if Path(state_path).exists() else {}
    cache = {}

    def history(ex, sym):
        if (ex, sym) not in cache:
            try:
                cache[(ex, sym)] = fm.history(ex, sym, days=3)
            except Exception:                                           # noqa: BLE001
                cache[(ex, sym)] = []
        return cache[(ex, sym)]

    state = step(state, radar.get("cross_funding") or [], history)
    Path(state_path).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(state)
    print(md)
    return md


if __name__ == "__main__":
    main(*sys.argv[1:3])
