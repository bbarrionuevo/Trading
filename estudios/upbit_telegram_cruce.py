"""Listados de Upbit con la hora REAL de su canal oficial de Telegram (7-nov, antes de separar la regla por tipo): el
estudio de `hipotesis_direccionales.py listados_upbit` mide desde la hora del SITIO; el agente entra desde el mensaje
de Telegram (que llega 0 a 2 s después y viene en segundos enteros), con el freno de la regla (no entra si ya subió más
que MAX_RISE desde la apertura del minuto del mensaje) y el tope de la orden (0,5 %).

1. Cada aviso del sitio con su mensaje de Telegram (misma moneda, el tipo en el texto, a menos de 10 min): la
   diferencia en segundos, por tipo (listado nuevo / alta en wones).
2. Como lo operaría el agente, entrando a `tg + demora` (la demora del relé hasta el agente y la orden), con freno de
   16 % (la regla de hoy), 25 % o sin freno: cuántas entran, cuántas llenan la orden con tope de 0,5 %, y lo que rinden
   a los 15 min (la salida de hoy) y a la hora, con el stop fijo de 10 %; por aviso (las que no entran o no se llenan
   cuentan 0) y las dos mitades por fecha.

Uso: python estudios/upbit_telegram_cruce.py <carpeta del estudio> <tg-upbit.json> [demoras, p. ej. 0.3,0.6,1.0]
(la carpeta del estudio es la de `hipotesis_direccionales.py`, con upbit-avisos.json y la caché de ventanas; el
archivo de Telegram sale de leer la vista pública t.me/s/upbit_news de a una página cada 3 s).
"""
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
DATA, TG = Path(sys.argv[1]), Path(sys.argv[2])
DELAYS = tuple(float(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "0.3,0.6,1.0").split(","))
STUDY = Path(__file__).with_name("hipotesis_direccionales.py")
BRAKES = (0.16, 0.25, None)
HOLDS = (900, 3600)
STOP, CAP = 0.10, 0.005
KIND_TEXT = {"listado_nuevo": "신규 거래지원", "alta_en_wones": "디지털 자산 추가", "alta_en_btc_o_usdt": "디지털 자산 추가"}


def study():
    sys.argv = [str(STUDY), "listados_upbit", str(DATA)]
    spec = importlib.util.spec_from_file_location("hip_listados_upbit", STUDY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.2f}%"


def match(events: list, msgs: list) -> list:
    """Cada aviso con el mensaje de Telegram más cercano de la misma moneda y tipo (a menos de 10 min)."""
    out = []
    for ev in events:
        coin, t0 = ev["coin"], ev["t0"] / 1000
        key = KIND_TEXT[ev["kind"]]
        cands = [m for m in msgs if f"({coin})" in m["text"] and key in m["text"] and abs(m["t"] - t0) <= 600]
        if cands:
            m = min(cands, key=lambda m: abs(m["t"] - t0))
            out.append({**ev, "tg": m["t"], "off": round(m["t"] - t0)})
    return out


def agent(tp, ev: dict, delay: float, brake, hold: int):
    """(entró, llenó, retorno) entrando a tg + delay como el agente: freno desde la apertura del minuto del mensaje."""
    tg_ms = int(ev["tg"] * 1000)
    i0, i = tp.first_at(tg_ms // 60000 * 60000), tp.first_at(tg_ms + delay * 1000)
    if i0 is None or i is None:
        return None
    rise = tp.p[i] / tp.p[i0] - 1
    if brake is not None and rise > brake:
        return False, False, None
    filled = tp.ioc(tg_ms, delay, 1, CAP)
    return True, filled, tp.run(tg_ms + delay * 1000, 1, hold, STOP)


def main() -> None:
    hip = study()
    msgs = json.loads(TG.read_text(encoding="utf-8"))
    first_tg = min(m["t"] for m in msgs)
    events = [e for e in hip.events_listados_upbit() if e["kind"] in ("listado_nuevo", "alta_en_wones")
              and e["t0"] / 1000 >= first_tg - 60]
    rows = match(events, msgs)
    print(f"Telegram: {len(msgs)} mensajes desde {time.strftime('%Y-%m-%d', time.gmtime(first_tg))}; avisos del sitio "
          f"en ese período: {len(events)}; con su mensaje: {len(rows)}")
    for kind in ("alta_en_wones", "listado_nuevo"):
        rs = [r for r in rows if r["kind"] == kind]
        if not rs:
            continue
        offs: dict[int, int] = {}
        for r in rs:
            offs[r["off"]] = offs.get(r["off"], 0) + 1
        print(f"\n== {kind}: {len(rs)} avisos con su mensaje · Telegram − sitio (s): "
              + " · ".join(f"{k:+d} s: {v}" for k, v in sorted(offs.items())))
        res: dict[tuple, list] = {}
        n_tape = 0
        for r in sorted(rs, key=lambda r: r["t0"]):         # cada ventana se lee una sola vez
            tp = hip.cached_tape(r)
            if tp is None:
                continue
            n_tape += 1
            for delay in DELAYS:
                for hold in HOLDS:
                    for brake in BRAKES:
                        res.setdefault((delay, hold, brake), []).append((r, agent(tp, r, delay, brake, hold)))
        print(f"   con perpetuo y ventana en la caché: {n_tape}", flush=True)
        if not n_tape:
            continue
        for delay in DELAYS:
            for hold in HOLDS:
                parts = []
                for brake in BRAKES:
                    got = [(r, g) for r, g in res[(delay, hold, brake)] if g is not None]
                    n = len(got)
                    entered = [(r, g) for r, g in got if g[0] and g[2] is not None]
                    rets = [g[2] for _, g in entered]
                    per_notice = sum(g[2] for _, g in entered if g[1]) / n if n else 0.0
                    half = n // 2
                    h1 = sum(g[2] for _, g in got[:half] if g[0] and g[1] and g[2] is not None) / max(half, 1)
                    h2 = sum(g[2] for _, g in got[half:] if g[0] and g[1] and g[2] is not None) / max(n - half, 1)
                    win = sum(1 for x in rets if x > 0) / len(rets) if rets else 0
                    parts.append(f"freno {'sin' if brake is None else f'{brake * 100:g} %'}: entran {len(entered)}/{n}"
                                 f" (llenan {sum(1 for _, g in entered if g[1])}) · mediana "
                                 f"{pct(statistics.median(rets)) if rets else '—'} · ganan {win:.0%} · por aviso "
                                 f"{pct(per_notice)} (mitades {pct(h1)} / {pct(h2)})")
                print(f"   tg + {delay:g} s, salida a los {hold // 60} min:")
                for p in parts:
                    print("      " + p, flush=True)


if __name__ == "__main__":
    main()
