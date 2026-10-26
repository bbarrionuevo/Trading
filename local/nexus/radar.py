"""Radar de oportunidades: varias estrategias, varios exchanges y P2P, en un solo informe ordenado.

python -m nexus.radar OUT_DIR
Escribe OUT_DIR/radar.md (informe en español), OUT_DIR/radar.json (última corrida) y agrega una
línea compacta a OUT_DIR/radar_history.jsonl para medir PERSISTENCIA: una oportunidad que aparece
una vez es ruido; la que se repite corrida tras corrida es la que vale la pena verificar y armar.
Nivel de evidencia: datos públicos en vivo. Nada de esto opera: solo informa.
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

from .discovery import funding_multi as fm
from .discovery import p2p_ar
from .discovery import yields


def _safe(fn, errors, name):
    try:
        return fn()
    except Exception as e:                                              # noqa: BLE001 (cada fuente es opcional)
        errors[name] = f"{type(e).__name__}: {str(e)[:120]}"
        return None


def collect():
    errors, res = {}, {"ts": time.time()}
    ar = _safe(p2p_ar.fetch, errors, "CriptoYa")
    if ar:
        rs = p2p_ar.rows(ar["quotes"][100])
        rs_big = p2p_ar.rows(ar["quotes"][1000])
        res["p2p_spreads"] = p2p_ar.p2p_spreads(rs)[:12]
        res["hedged_ads"] = p2p_ar.hedged_ads(rs)[:12]
        res["hedged_ads_1000"] = p2p_ar.hedged_ads(rs_big)[:6]
        res["rulo"] = p2p_ar.rulo(ar["dolar"], ar["bancos"], rs, ar.get("usd"))
        res["banks"] = p2p_ar.fresh_banks(ar["bancos"])[:6]
        k = _safe(p2p_ar.kraken_usdt_usd, errors, "Kraken USDT/USD")
        res["cross_border"] = p2p_ar.cross_border(rs, ar["bancos"], k, mep=p2p_ar._mep(ar["dolar"]))
        conv = p2p_ar.usd_conversion_table({"USDT": ar.get("usd"), "USDC": ar.get("usd_USDC")})
        res["usd_conversion"] = conv[:10]
        bids = {}
        for coin, quotes in (("USDT", ar["quotes"][100]), ("USDC", ar.get("ars_USDC") or {})):
            rr = p2p_ar.rows(quotes)
            if rr:
                b = max(rr, key=lambda r: r[4])
                bids[coin] = (b[0], b[4])
        banks = p2p_ar.fresh_banks(ar["bancos"])
        routes = ([("oficial", banks[0][1])] if banks else []) + \
                 ([("MEP", p2p_ar._mep(ar["dolar"]))] if p2p_ar._mep(ar["dolar"]) else [])
        res["local_loops"] = p2p_ar.local_loops(conv, bids, routes)[:8]
        res["apps"] = sorted(([r[0], r[2], r[4]] for r in rs if not r[5]), key=lambda x: -x[2])[:8]
    rows, ferr = fm.fetch_all()
    errors.update({f"funding {k}": v for k, v in ferr.items()})
    res["funding_venues"] = dict(Counter(r["exchange"] for r in rows))
    first = fm.cross_spreads(rows)
    cand = {x["base"] for x in first[:40]} | {r["base"] for r in sorted(rows, key=lambda r: -abs(r["apr"]))[:20]}
    rows = fm.refine_intervals(rows, cand)
    allx = fm.cross_spreads(rows)
    res["cross_funding"] = fm.verify_top([x for x in allx if not x["extreme"]][:20], rows)
    res["cross_extreme"] = [x for x in allx if x["extreme"]][:10]
    liquid = [r for r in rows if r["vol_usd"] >= 1_000_000]
    res["top_funding"] = [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()}
                          for r in sorted(liquid, key=lambda r: -r["apr"])[:15]]
    lend, lerr = _safe(yields.lending, errors, "préstamos") or ([], {})
    errors.update(lerr)
    res["lending"] = lend
    res["defi"] = _safe(yields.defi_yields, errors, "DefiLlama") or []
    res["launchpools"] = _safe(yields.mexc_launchpools, errors, "MEXC launchpool") or []
    res["events"] = _safe(yields.bitget_events, errors, "Bitget anuncios") or []
    res["errors"] = errors
    return res


def history_line(res):
    r = res.get("rulo") or {}
    return {"ts": res["ts"], "rulo": {x["route"].split(" ")[0]: x["net_pct"] for x in r.get("routes", [])},
            "cross_border": (res.get("cross_border") or {}).get("gross_pct"),
            "usd_conv_best": (res.get("usd_conversion") or [{}])[0].get("premium_pct"),
            "local_loop_best": (res.get("local_loops") or [{}])[0].get("net_pct"),
            "p2p_best": {x["platform"]: x["net_pct"] for x in res.get("p2p_spreads", [])[:6]},
            "hedged_best": [(x["platform"], x["side"][:14], x["net_pct"]) for x in res.get("hedged_ads", [])[:4]],
            "cross": {x["base"]: [x["short"], x["long"], x["spread_apr"]] for x in res.get("cross_funding", [])[:15]},
            "cross_extreme": {x["base"]: x["spread_apr"] for x in res.get("cross_extreme", [])[:10]},
            "lending": {f"{x['venue']} {x['asset']}": x["apr_pct"] for x in res.get("lending", [])}}


def persistence(hist, min_spread=20.0):
    """Cuántas corridas aparece cada diferencia de funding con APR >= min_spread y su promedio."""
    n = len(hist)
    seen, total = Counter(), Counter()
    for h in hist:
        for base, (_, _, sp) in (h.get("cross") or {}).items():
            if sp >= min_spread:
                seen[base] += 1
                total[base] += sp
    return n, [(b, c, round(total[b] / c, 1)) for b, c in seen.most_common(12)]


def to_markdown(res, hist):
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(res["ts"]))
    md = [f"# Radar de oportunidades — {ts}", "",
          "Datos públicos en vivo. Cada oportunidad es **a verificar** (comisiones reales, cupos, tiempos y "
          "normativa) antes de poner dinero. La columna clave es la *persistencia*: lo que se repite.", ""]
    r = res.get("rulo")
    if r and r.get("routes"):
        md += ["## 1. Argentina: pesos → dólares → USDT → pesos (\"rulo\")", "",
               "| compra de dólares | ARS por USD | pasa a USDT en | USD por USDT | vende USDT en | ARS por USDT | bruto % | neto % |",
               "|---|---|---|---|---|---|---|---|"]
        md += [f"| {x['route']} | {x['usd_ars']:,.2f} | {x['conv_at']} | {x['usd_per_usdt']} | {x['sell_at']} | "
               f"{x['usdt_bid']:,.2f} | {x['gross_pct']:+.2f} | **{x['net_pct']:+.2f}** |" for x in r["routes"]]
        md += ["", f"Referencias: oficial {r.get('oficial')} · blue (compra) {r.get('blue')} · MEP {r.get('mep')} · "
               f"cripto USDT {r.get('cripto_usdt')}",
               "", "A verificar antes de operar: cupo de dólares de tu banco, si la plataforma acepta tus dólares por "
               "transferencia y cuánto tarda, impuestos, y la normativa vigente (restricciones cruzadas).", ""]
    conv = res.get("usd_conversion") or []
    if conv:
        md += ["### Costo de pasar dólares a stablecoin dentro de Argentina (la fricción que sostiene la brecha)", "",
               "| plataforma | moneda | USD por unidad | prima % |", "|---|---|---|---|"]
        md += [f"| {c['platform']} | {c['coin']} | {c['usd_per_unit']} | {c['premium_pct']:+.2f} |" for c in conv[:8]]
        loops = res.get("local_loops") or []
        if loops:
            b = loops[0]
            md += ["", f"Mejor rulo **dentro del país**: dólar {b['route']} → {b['coin']} en {b['convert_at']} → pesos en "
                   f"{b['sell_at']}: **{b['net_pct']:+.2f}% por vuelta** (si alguna plataforma baja su prima a ~1-2%, "
                   "este rulo se vuelve automatizable sin giros al exterior)."]
        md.append("")
    cb = res.get("cross_border")
    if cb:
        sizes = " · ".join(f"{n:,} USD: **{v:+.2f}%**" for n, v in cb.get("by_size", {}).items())
        md += ["### Vuelta internacional (usa tus cuentas del exterior)", "",
               f"USD afuera → USDT en Kraken a {cb['usdt_usd_ask']} (+0,26%) → vender en **{cb['sell_at']}** a "
               f"{cb['usdt_bid']:,.2f} ARS → dólar oficial en **{cb['bank']}** a {cb['usd_ars']:,.2f} → giro al exterior: "
               f"**{cb['gross_pct']:+.2f}% bruto por vuelta**.",
               f"Neto por vuelta según el monto (giro supuesto: {cb.get('wire_fixed_usd', 0):.0f} USD fijos + "
               f"{cb.get('wire_var_pct', cb.get('wire_pct'))}%): {sizes or cb.get('net_pct')}"]
        if cb.get("mep_by_size"):
            md.append(f"Con dólar MEP (automatizable por API de un broker): bruto {cb['mep_gross_pct']:+.2f}% · " +
                      " · ".join(f"{n:,} USD: {v:+.2f}%" for n, v in cb["mep_by_size"].items()))
        md += ["", "Automatizable: comprar USDT en Kraken, enviarlo y venderlo por pesos (APIs). Manual: comprar los "
               "dólares (salvo MEP por broker) y el giro al exterior; conviene juntarlo en pocas vueltas grandes. "
               "A verificar: costo real del giro de tu banco y de la acreditación en Kraken, y tus límites "
               "(UE < 10.000 € acumulados, AR < 50 M ARS/mes, 2,5 M ARS por operación).", ""]
    if res.get("p2p_spreads"):
        md += ["## 2. P2P: margen del que publica avisos (merchant)", "",
               "| plataforma | mejor compra (bid) | mejor venta (ask) | bruto % | neto % (2 x 0,2% comisión) |",
               "|---|---|---|---|---|"]
        md += [f"| {x['platform']} | {x['bid']:,.2f} | {x['ask']:,.2f} | {x['gross_pct']:+.2f} | {x['net_pct']:+.2f} |"
               for x in res["p2p_spreads"]]
        md += ["", "### Aviso P2P cubierto al instante en una app (por llenado)", "",
               "| plataforma | estrategia | precio del aviso | cubre en | precio | neto % |", "|---|---|---|---|---|---|"]
        md += [f"| {x['platform']} | {x['side']} | {x['ad_price']:,.2f} | {x['hedge_at']} | {x['hedge_price']:,.2f} | "
               f"{x['net_pct']:+.2f} |" for x in res.get("hedged_ads", [])[:8]]
        md.append("")
    cf = res.get("cross_funding") or []
    if cf:
        n, pers = persistence(hist)
        md += ["## 3. Diferencia de funding entre exchanges (delta neutral, sin contado)", "",
               f"Fuentes: {res.get('funding_venues')}. Volumen 24 h ≥ 1 M USD en ambas patas. Neto a 7 días tras 4 "
               "ejecuciones tomadoras.", "",
               "| moneda | corto en (APR) | largo en (APR) | diferencia APR % ahora | REAL últimos días: total % · APR % · días + | costo % | días para cubrir costo | intervalos conocidos |",
               "|---|---|---|---|---|---|---|---|"]

        def _real(x):
            r = x.get("realized")
            return f"{r[0]:+.2f} · {r[1]:+.0f} · {r[2]:.0%} ({r[3]} d)" if r else "—"
        md += [f"| {x['base']} | {x['short']} ({x['short_apr']}) | {x['long']} ({x['long_apr']}) | **{x['spread_apr']}** | "
               f"{_real(x)} | {x['cost_pct']} | {x['breakeven_days']} | {'sí' if x['intervals_known'] else 'no'} |"
               for x in cf[:15]]
        ex = res.get("cross_extreme") or []
        if ex:
            md += ["", f"**Extremos** (alguna pata > {fm.EXTREME_APR:.0f}% APR: tokens nuevos o squeezes; pueden "
                   "darse vuelta en horas, alto riesgo): " +
                   ", ".join(f"{x['base']} {x['spread_apr']}% ({x['short']}/{x['long']})" for x in ex[:8])]
        if n > 1:
            md += ["", f"**Persistencia** (últimas {n} corridas, diferencia ≥ 20% APR): " +
                   ", ".join(f"{b} {c}/{n} (prom. {a}%)" for b, c, a in pers)]
        md.append("")
    if res.get("top_funding"):
        md += ["### Funding más alto (para carry con contado o como pata corta)", "",
               "| exchange | moneda | APR % | intervalo h | volumen 24 h USD |", "|---|---|---|---|---|"]
        md += [f"| {x['exchange']} | {x['base']} | {x['apr']:.1f} | {x['interval_h']}{'' if x['interval_known'] else '?'} | "
               f"{x['vol_usd']:,.0f} |" for x in res["top_funding"][:10]]
        md.append("")
    if res.get("lending"):
        md += ["## 4. Préstamo de stablecoins (sin riesgo de precio; riesgo del exchange)", "",
               "| dónde | activo | APR % | nota |", "|---|---|---|---|"]
        md += [f"| {x['venue']} | {x['asset']} | {x['apr_pct']} | {x['note']} |" for x in res["lending"]]
        md.append("")
    if res.get("defi"):
        md += ["### DeFi: stablecoins en protocolos grandes (sin pérdida impermanente, TVL ≥ 20 M USD)", "",
               "APY base = intereses o comisiones reales; los premios en tokens van aparte (se pueden cortar). "
               "Riesgo: contratos y depeg. Sirve para la plata que no está trabajando.", "",
               "| protocolo | tipo | red | pool | TVL M USD | APY base % | premios % | APY medio 30 d % |",
               "|---|---|---|---|---|---|---|---|"]
        defi = [x for x in res["defi"] if x.get("bluechip")][:6] + [x for x in res["defi"] if not x.get("bluechip")][:5]
        md += [f"| {x['project']} | {'consolidado' if x.get('bluechip') else 'nuevo / sintético'} | {x['chain']} | {x['symbol']} | "
               f"{x['tvl_musd']} | {x['apy_base']} | {x['apy_reward']} | {x['apy_30d_mean']} |" for x in defi]
        md.append("")
    lp = res.get("launchpools") or []
    ev = res.get("events") or []
    md += ["## 5. Eventos: launchpools y promociones", ""]
    if lp:
        md += ["| MEXC launchpool | estado | termina | depositar | tipo | quién | APR |", "|---|---|---|---|---|---|---|"]
        md += [f"| {x['activity']} ({x['coin']}) | {x['status']} | {x['ends']} | {x['stake']} | {x['pool_type']} | "
               f"{x['join']} | {x['apr'] if x['apr'] is not None else '?'} |" for x in lp[:12]]
    else:
        md.append("Sin launchpools activos de MEXC en este momento.")
    if ev:
        md += ["", "Anuncios recientes de Bitget:"] + [f"- {x['date']} [{x['title']}]({x['url']})" for x in ev[:8]]
    if res.get("errors"):
        md += ["", f"Fuentes con error: {json.dumps(res['errors'], ensure_ascii=False)}"]
    return "\n".join(md) + "\n"


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res = collect()
    hist_path = out / "radar_history.jsonl"
    with open(hist_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(history_line(res), ensure_ascii=False) + "\n")
    hist = [json.loads(x) for x in hist_path.read_text(encoding="utf-8").splitlines()[-200:] if x.strip()]
    md = to_markdown(res, hist)
    (out / "radar.md").write_text(md, encoding="utf-8")
    (out / "radar.json").write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main(sys.argv[1])
