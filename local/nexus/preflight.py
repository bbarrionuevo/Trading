"""Chequeo previo del dinero real, SOLO LECTURA: ¿está todo listo para operar en MEXC?

Revisa sin enviar órdenes:
- clave: presente, con permiso de trading, retiros según la cuenta y saldo de USDT en la subcuenta;
- reloj de la PC frente al de MEXC (si se desfasa más de ~5 s, MEXC rechaza las órdenes firmadas);
- latencia a MEXC (mediana de varias consultas: dato para el criterio C6 de R-15);
- autorización local vigente;
- cada par del dinero real (autorizado o elegido en modo automático) o candidato: estado, tipos de orden, monto mínimo, precisión, tick en % del
  precio, spread actual y comisiones reales.
Resultado en state/preflight.json (va en el latido) y en pantalla.
  python -m nexus.preflight [PAR1,PAR2]
"""
import statistics
import sys
import time

from . import paths

ONLINE = ("1", "ENABLED", "TRADING")


def symbol_check(client, sym, notional):
    """Reglas y condiciones de un par para el dinero real. ok=False dice por qué no conviene."""
    out = {"sym": sym, "problems": []}
    try:
        info = next(x for x in client._get("/api/v3/exchangeInfo", {"symbol": sym})["symbols"] if x["symbol"] == sym)
    except Exception as e:                                           # noqa: BLE001
        return out | {"ok": False, "problems": [f"sin exchangeInfo ({type(e).__name__})"]}
    types = info.get("orderTypes") or []
    out.update(base=info.get("baseAsset"), status=info.get("status"), order_types=types,
               price_dp=info.get("quotePrecision"),
               qty_dp=info.get("baseAssetPrecision"), min_quote=float(info.get("quoteAmountPrecision") or 0))
    if str(info.get("status")) not in ONLINE or info.get("isSpotTradingAllowed") is False:
        out["problems"].append(f"par no operable (status {info.get('status')})")
    for t in ("LIMIT_MAKER", "LIMIT"):
        if t not in types:
            out["problems"].append(f"no admite {t}")
    if out["min_quote"] > notional / 2:
        out["problems"].append(f"monto mínimo {out['min_quote']} USDT demasiado alto para {notional}")
    try:
        bt = client.book_ticker(sym)
        bid, ask = float(bt["bidPrice"]), float(bt["askPrice"])
        tick = 10 ** -int(info.get("quotePrecision") or 8)
        out.update(bid=bid, ask=ask, spread_pct=round((ask / bid - 1) * 100, 4), tick_pct=round(tick / bid * 100, 4))
    except Exception as e:                                           # noqa: BLE001
        out["problems"].append(f"sin libro ({type(e).__name__})")
    try:
        from .exchanges.mexc import parse_fee
        f = parse_fee(client.trade_fee(sym)) if client.has_keys else None
        out["fees_pct"] = f
        if f and f[0] > 0:
            out["problems"].append(f"comisión maker {f[0]}% (la estrategia supone 0%)")
    except Exception as e:                                           # noqa: BLE001
        out["fees_pct"] = f"sin dato ({type(e).__name__})"
    out["ok"] = not out["problems"]
    return out


def latency_ms(client, sym="BTCUSDT", n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            client.book_ticker(sym)
            xs.append((time.perf_counter() - t0) * 1000)
        except Exception:                                            # noqa: BLE001
            pass
    return {"median_ms": round(statistics.median(xs)), "max_ms": round(max(xs)), "n": len(xs)} if xs else None


def run(client=None, symbols=(), clock=None, now=None):
    from .agent.live import MAX_CAPITAL_EUR, authorization
    from .exchanges.mexc import MexcReadOnly
    client = client or MexcReadOnly()
    now = now or time.time()
    auth = authorization(now)
    capital = float((auth or {}).get("capital_eur") or MAX_CAPITAL_EUR)
    r = {"ts": now, "auth": auth, "keys": client.has_keys, "problems": []}
    bal = {}
    if not client.has_keys:
        r["problems"].append("no hay clave de MEXC en .env")
    else:
        try:
            acc = client.account()
            bal = {b["asset"]: float(b.get("free") or 0) + float(b.get("locked") or 0) for b in acc.get("balances", [])}
            r["account"] = {"canTrade": acc.get("canTrade"), "canWithdraw": acc.get("canWithdraw"),
                            "accountType": acc.get("accountType"), "usdt": round(bal.get("USDT", 0.0), 4),
                            "other_assets": sorted(a for a, v in bal.items() if v > 0 and a != "USDT")}
            if acc.get("canTrade") is False:
                r["problems"].append("la clave no tiene permiso de trading")
        except Exception as e:                                       # noqa: BLE001
            from .check_keys import describe_error
            r["problems"].append(f"la clave no responde: {describe_error(e)}")
    try:
        from .check_keys import clock_offset_ms
        off = (clock or clock_offset_ms)("MEXC")
        r["clock_offset_ms"] = round(off)
        if abs(off) > 2000:
            r["problems"].append(f"reloj de la PC desfasado {off / 1000:+.1f} s: sincronizar Windows")
    except Exception as e:                                           # noqa: BLE001
        r["clock_offset_ms"] = f"sin dato ({type(e).__name__})"
    r["latency"] = latency_ms(client)
    # pares del dinero real: los autorizados o, en modo automático, los promovidos que elige el agente,
    # más los que quedaron con posición guardada
    from .agent.live import STATE_FILE
    from .agent.maker_agent import desk_symbols
    real = desk_symbols(auth, paths.read_json(paths.state(STATE_FILE), {})) if auth else []
    r["real_symbols"] = real
    if auth and auth.get("auto") and not real:
        r["problems"].append("modo automático sin pares promovidos todavía: el agente sigue en paper")
    syms = list(dict.fromkeys(real + list(symbols)))
    r["symbols"] = [symbol_check(client, s, capital) for s in syms]
    if auth and r.get("account"):
        # capital = USDT + lo invertido en los pares reales (con una posición abierta el USDT baja)
        held = sum(bal.get(x.get("base"), 0.0) * (x.get("bid") or 0.0) for x in r["symbols"] if x["sym"] in real)
        r["account"]["in_positions_usdt"] = round(held, 4)
        r["account"]["capital_usdt"] = round(bal.get("USDT", 0.0) + held, 4)
        if auth.get("todo"):                        # --todo: se opera lo que haya; solo alarma si no alcanza
            if r["account"]["capital_usdt"] < 5:
                r["problems"].append(f"capital en la cuenta {r['account']['capital_usdt']:.2f} USDT: no alcanza para operar")
        elif bal.get("USDT", 0.0) + held < capital * 0.95:
            r["problems"].append(f"capital en la cuenta {bal.get('USDT', 0.0) + held:.2f} USDT menor que el autorizado {capital:g}")
    r["ready"] = bool(auth) and not r["problems"] and all(x["ok"] for x in r["symbols"] if x["sym"] in real)
    paths.write_json_atomic(paths.state("preflight.json"), r)
    return r


def describe(r):
    lines = [f"Chequeo previo {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))}: "
             f"{'LISTO para operar en real' if r['ready'] else 'no está listo para operar en real'}"]
    if r.get("account"):
        a = r["account"]
        lines.append(f"- Cuenta: trading {a['canTrade']}, retiros (dato de la cuenta) {a['canWithdraw']}, USDT {a['usdt']}")
    lines.append(f"- Reloj: {r.get('clock_offset_ms')} ms · latencia: {r.get('latency')}")
    lines.append(f"- Autorización: {r['auth'] or 'ninguna (solo paper)'}")
    for p in r["problems"]:
        lines.append(f"- PROBLEMA: {p}")
    for s in r["symbols"]:
        lines.append(f"- {s['sym']}: {'OK' if s['ok'] else 'NO'} spread {s.get('spread_pct')}% tick {s.get('tick_pct')}% "
                     f"mínimo {s.get('min_quote')} USDT comisiones {s.get('fees_pct')}"
                     + (f" — {'; '.join(s['problems'])}" if s["problems"] else ""))
    return "\n".join(lines)


def main(argv=None):
    paths.load_env()
    argv = sys.argv[1:] if argv is None else argv
    r = run(symbols=[s.strip().upper() for s in (argv[0] if argv else "").split(",") if s.strip()])
    print(describe(r))
    return 0 if r["ready"] or not r["auth"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
