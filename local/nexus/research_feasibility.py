"""Viabilidad real de dos oportunidades con datos públicos (sin claves):

1. Carry de funding: historial de tasas liquidadas de los perpetuos con contado en el mismo exchange.
2. Cobertura MEXC->Binance: si la moneda se puede mover entre exchanges (depósitos y retiros por red).

python -m nexus.research_feasibility OUT_DIR [top_funding] [top_hedge]
Escribe OUT_DIR/feasibility.md y OUT_DIR/feasibility.json.
"""
import json
import sys
import time
from pathlib import Path

from .discovery import funding_history as fh
from .discovery import networks as nw
from .discovery import sources
from .discovery.hedge import scan_hedge

QUOTES = ("USDT", "USDC", "USD1", "FDUSD")
# Persistentes en el informe del 24-oct-2026 (se agregan a los del momento)
KNOWN_HEDGE = ("IOST", "ATM", "DCR", "BMT", "MANTA", "HOME", "TFUEL", "EGLD", "ZEC", "AT", "QUICK", "TREE", "PORTO")


def base_of(sym):
    for q in QUOTES:
        if sym.endswith(q) and len(sym) > len(q):
            return sym[: -len(q)]
    return None


def funding_section(top=25, min_volume=1e6):
    rows = sources.mexc_funding()
    spot = sources.mexc_book_all()
    cands = sorted((r for r in rows if r[2] > 0 and r[4] >= min_volume
                    and f"{r[1].split('_')[0]}USDT" in spot), key=lambda r: -r[2])[:top]
    out = []
    for _, sym, rate, _, vol in cands:
        try:
            hist = fh.mexc_history(sym)
            cycle = fh.mexc_cycle_hours(sym)
            st = fh.carry_stats(hist)
        except Exception as e:                                        # noqa: BLE001
            out.append({"exchange": "MEXC", "pair": sym, "error": str(e)[:100]})
            continue
        out.append({"exchange": "MEXC", "pair": sym, "rate_now_pct": round(rate * 100, 4), "cycle_h": cycle,
                    "volume_24h_usdt": round(vol), "stats": st, "verdict": fh.verdict(st)})
        time.sleep(0.2)
    try:
        g_rows = sources.gate_funding()
        g_spot = sources.gate_spot_pairs()
        g_cands = sorted((r for r in g_rows if r[2] > 0 and r[4] >= min_volume and r[1] in g_spot),
                         key=lambda r: -r[2])[:10]
        for _, sym, rate, _, vol in g_cands:
            st = fh.carry_stats(fh.gate_history(sym))
            out.append({"exchange": "Gate", "pair": sym, "rate_now_pct": round(rate * 100, 4), "cycle_h": None,
                        "volume_24h_usdt": round(vol), "stats": st, "verdict": fh.verdict(st)})
            time.sleep(0.2)
    except Exception as e:                                            # noqa: BLE001
        out.append({"exchange": "Gate", "pair": "-", "error": str(e)[:100]})
    return out


def hedge_section(top=25, min_depth=200):
    hedge = scan_hedge(sources.mexc_book_all(), sources.binance_book_all(), 0.0, 0.1)
    coins = []
    for h in hedge:
        b = base_of(h["pair"])
        if b and h["net_pct"] >= 0.2 and h["hedge_depth_usdt"] >= min_depth and b not in coins:
            coins.append(b)
    edges = {}
    for h in hedge:
        b = base_of(h["pair"])
        if b and h["net_pct"] > edges.get(b, (-1e9,))[0]:
            edges[b] = (h["net_pct"], h["pair"], h["side"])
    coins = list(dict.fromkeys(coins[:top] + [c for c in KNOWN_HEDGE if c in edges]))
    nets, errors = nw.fetch_all()
    rows = []
    for c in coins + ["USDT"]:
        row = {"coin": c, "edge": edges.get(c), "status": {ex: nw.status(n, c) for ex, n in nets.items()}}
        if "MEXC" in nets and "Binance" in nets:
            row["MEXC->Binance"] = nw.best_route(nets["MEXC"], nets["Binance"], c)
            row["Binance->MEXC"] = nw.best_route(nets["Binance"], nets["MEXC"], c)
        rows.append(row)
    return rows, errors, sorted(nets)


def _fmt_stats(s, key):
    d = (s or {}).get(key)
    return f"{d['apr_pct']:.0f}% · {d['positive_share']:.0%}+ · neto {d['net_pct']:+.2f}%" if d else "—"


def to_markdown(funding, hedge_rows, net_errors, net_ok):
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    md = [f"# Viabilidad con datos reales — {ts}", "",
          "## 1. Carry de funding (largo contado + corto perpetuo), historial liquidado", "",
          "APR = lo cobrado realmente en la ventana, anualizado. `+` = fracción de cobros positivos. "
          "Neto = cobrado en la ventana menos 0,2% de entrada+salida. Peor 24 h = suma mínima en un día.", "",
          "| exchange par | ciclo h | tasa ahora % | 7 días | 30 días | peor 24 h % | días de historial | veredicto |",
          "|---|---|---|---|---|---|---|---|"]
    for f in sorted(funding, key=lambda f: (f.get("verdict") != "candidato",
                                            -(((f.get("stats") or {}).get("d30") or {}).get("apr_pct") or -1e9))):
        if "error" in f:
            md.append(f"| {f['exchange']} {f['pair']} | error: {f['error']} |||||||")
            continue
        s = f["stats"] or {}
        md.append(f"| {f['exchange']} {f['pair']} | {s.get('interval_h') or f.get('cycle_h') or '?'} | "
                  f"{f['rate_now_pct']} | {_fmt_stats(s, 'd7')} | {_fmt_stats(s, 'd30')} | "
                  f"{s.get('worst_24h_pct', '—')} | {s.get('days_covered', '—')} | **{f['verdict']}** |")
    md += ["", "## 2. Cobertura MEXC→Binance: ¿se puede mover la moneda?", "",
           f"Fuentes públicas que respondieron: {', '.join(net_ok) or 'ninguna'}. "
           f"Sin respuesta: {json.dumps(net_errors, ensure_ascii=False) if net_errors else 'ninguna'}.", ""]
    exs = net_ok
    md.append("| moneda | mejor margen % (par, lado) | " + " | ".join(exs) + " | MEXC→Binance | Binance→MEXC |")
    md.append("|---|---|" + "---|" * len(exs) + "---|---|")
    for r in hedge_rows:
        e = r["edge"]
        edge = f"{e[0]:.2f} ({e[1]}, {e[2]})" if e else "—"
        routes = [r.get(k) for k in ("MEXC->Binance", "Binance->MEXC")]
        routes = [f"{x[0]} fee {x[1]}" if x else ("sin ruta" if k in r else "?")
                  for x, k in zip(routes, ("MEXC->Binance", "Binance->MEXC"))]
        md.append(f"| {r['coin']} | {edge} | " + " | ".join(r["status"].get(x, "?") for x in exs)
                  + f" | {routes[0]} | {routes[1]} |")
    md += ["", "Lectura: una brecha que persiste con depósito o retiro cerrado en un lado no es "
           "aprovechable con rebalanceo; con ambos abiertos, el límite es capital y costo de transferencia."]
    return "\n".join(md) + "\n"


def main(out_dir, top_funding=25, top_hedge=25):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    funding = funding_section(int(top_funding))
    hedge_rows, errors, ok = hedge_section(int(top_hedge))
    md = to_markdown(funding, hedge_rows, errors, ok)
    (out / "feasibility.md").write_text(md, encoding="utf-8")
    (out / "feasibility.json").write_text(json.dumps(
        {"ts": time.time(), "funding": funding, "hedge": hedge_rows, "network_errors": errors},
        ensure_ascii=False, default=str), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main(*sys.argv[1:])
