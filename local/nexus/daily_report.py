"""Informe diario automático: qué clases de oportunidad dieron ROI neto positivo (nivel: observado).

No suma "euros posibles": una misma oportunidad persistente aparece en muchas muestras y
sumarlas la contaría varias veces (hallazgo F-12). Se informan muestras y oportunidades distintas.

python -m nexus.daily_report data/discovery.jsonl > data/report.md
"""
import json
import sys
import time


def _net(x):
    return float(x.get("net_pct", x.get("edge_pct")))


def _label(x):
    return x.get("path") or x.get("pair") or x.get("venue") or f"{x.get('buy_at')}>{x.get('sell_at')}"


def best(rec, key):
    v = rec.get(key) or []
    return max((_net(x) for x in v), default=None)


def persistence(rows, key, ident, value, min_share=0.5):
    """Oportunidades que se repiten: en qué fracción de las muestras aparecen y con qué valor medio."""
    seen, vals = {}, {}
    for r in rows:
        for x in r.get(key) or []:
            k = ident(x)
            seen[k] = seen.get(k, 0) + 1
            vals.setdefault(k, []).append(value(x))
    n = len([r for r in rows if r.get(key)])
    out = [(k, c / n, sum(vals[k]) / len(vals[k]), min(vals[k])) for k, c in seen.items() if n and c / n >= min_share]
    return n, sorted(out, key=lambda t: (t[1], t[2]), reverse=True)


def main(path, hours=24):
    since = time.time() - hours * 3600
    rows = [r for r in map(json.loads, open(path)) if r["ts"] >= since]
    keys = ["hedge_mexc_binance", "hedge_mexc_binance_bnb", "funding", "maker_kraken", "maker_binance", "peg_kraken", "peg_binance", "bin_tri_USDT", "bin_tri_BTC", "bin_tri_ARS", "bin_tri_EUR", "tri_EUR", "tri_USDC", "cross_eur", "eurc_arb", "ars_USDT", "ars_USDC", "ars_BTC", "ars_ETH"]
    print(f"# Informe nexus — últimas {hours} h, {len(rows)} muestras\n")
    print("| clase | muestras | muestras con neto>0 | oportunidades distintas >0 | mejor neto % |")
    print("|---|---|---|---|---|")
    for k in keys:
        vals = [b for b in (best(r, k) for r in rows) if b is not None]
        distinct = {_label(x) for r in rows for x in (r.get(k) or []) if _net(x) > 0}
        print(f"| {k} | {len(vals)} | {sum(v > 0 for v in vals)} | {len(distinct)} | "
              f"{max(vals, default=float('nan')):.3f} |")
    tri = [r.get("tri_EUR_maxgross") for r in rows if r.get("tri_EUR_maxgross")]
    if tri:
        print(f"\nTriangular EUR: ineficiencia bruta máx {max(tri):.3f}% "
              "(si una ruta sin comisión la supera, el cuello de botella es el fee).")
    n, fund = persistence(rows, "funding", lambda x: f"{x['exchange']} {x['pair']}"
                          + ("" if x.get("spot_same_exchange", True) else " (sin contado)"), lambda x: x["apr_pct"])
    if fund:
        print(f"\n### Funding persistente ({n} muestras): aparece en ≥50% de las muestras")
        print("| exchange par | presencia | APR medio % | APR mínimo % |\n|---|---|---|---|")
        for k, share, avg, mn in fund[:12]:
            print(f"| {k} | {share:.0%} | {avg:.1f} | {mn:.1f} |")
    n, hed = persistence(rows, "hedge_mexc_binance", lambda x: f"{x['pair']} {x['side']}", lambda x: x["net_pct"])
    if hed:
        print(f"\n### Cobertura MEXC→Binance persistente ({n} muestras, taker 0,1%)")
        print("| par lado | presencia | margen medio % | margen mínimo % |\n|---|---|---|---|")
        for k, share, avg, mn in hed[:12]:
            print(f"| {k} | {share:.0%} | {avg:.3f} | {mn:.3f} |")
    errs = {}
    for r in rows:
        for k in r.get("errors", {}):
            errs[k] = errs.get(k, 0) + 1
    if errs:
        print(f"\nErrores de fuente: {errs}")


if __name__ == "__main__":
    main(sys.argv[1], *map(float, sys.argv[2:]))
