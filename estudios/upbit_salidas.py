"""Listados de Upbit al instante: ¿salir a los 15 min es vender en plena corrección? (pedido del dueño, 6-nov:
"Retorno mediano y win rate saliendo a: 30 s, 60 s, 2 min, 5 min y 15 min. Momento del pico (MFE) [...] ¿qué números da
salir a los 45-60 s versus usar un trailing stop dinámico?").

Los mismos 31 listados y ventanas de `estudios/upbit_segundos.py` (cada operación del perpetuo de Binance, de 1 min
antes a 65 min después del aviso del sitio) y las demoras medidas desde Tokio: Telegram 0,5 / 1 / 2 s después del sitio
(mediana 1 s) y 0,05 s hasta la orden. Entrada: la primera operación desde ahí, +0,1 % de deslizamiento. El stop fijo de
10 % (A-5) siempre puesto; cuando un stop salta, se vende al precio de la operación que lo cruzó, −0,1 %. 0,2 % de costos.

1. Salida fija a los 30 s, 45 s, 60 s, 90 s, 2, 3, 5, 10, 15 (la regla de hoy), 30 y 60 min de la entrada.
2. El pico (MFE): en qué segundo después del aviso toca el techo (cierre de cada segundo), cuánto sube desde la
   entrada y cuánto devolvió a los 15 min.
3. Stop que sigue al pico (trailing): vende si cae X % desde el máximo desde la entrada, con el stop de 10 % debajo y
   como mucho 15 o 60 min; uno "dinámico" (8 % los primeros 2 min, después 4 %); y mitad a los 60 s + mitad a los 15 min.
4. ¿Se sostiene? Cada salida contra la de hoy, listado por listado (en cuántos gana y la diferencia media con su rango
   del 90 % por remuestreo), la primera y la segunda mitad de los listados por fecha, y la media sin el mejor.
5. La cuenta: 100 USDT de mentira, 0,30 veces el saldo por operación (la regla de hoy), los 31 en fila.

Resultado (6-nov, a 1 s de Telegram): salir a los 15 min da la mayor media (+12,02 %, mediana +9,16 %, 71 %); salir a
los 45-60 s, +10,4 a +10,6 % (74 %); a los 2-5 min, 1,5 a 1,8 puntos menos que a los 15 min (rango del 90 % debajo de 0);
el stop que sigue al pico (3-8 %), +5,0 a +6,6 % (el sacudón de los primeros segundos lo saca y se pierde la segunda
subida). El techo: 21 de 31 en los primeros 30 s del aviso, 9 entre los 5 y los 15 min. Sin cambio. BABY (5-jun) entra a
+46,8 % por operaciones rarísimas del primer segundo (hasta +10.000 %) y pierde −22 % en todas las salidas por igual.

Uso: python estudios/upbit_salidas.py <carpeta con listados-upbit.json y upbit_segundos/>
"""
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1])
COST, SLIP, STOP, X = 0.002, 0.001, 0.10, 0.30
TG_S, TG_MAIN, ORDER_S = (0.5, 1.0, 2.0), 1.0, 0.05
FIXED_S = (30, 45, 60, 90, 120, 180, 300, 600, 900, 1800, 3600)
RULE_S = 900
BOOT = 5000


def load():
    events = {int(float(e["t"]) * 1000): e for e in json.loads((D / "listados-upbit.json").read_text(encoding="utf-8"))
              ["eventos"]}
    out = []
    for f in (D / "upbit_segundos").glob("*.json"):
        t0 = int(f.stem.rsplit("-", 1)[1])
        tr = json.loads(f.read_text(encoding="utf-8"))
        pre = [p for t, p in tr if t < t0]
        if not pre or t0 not in events:
            continue
        ref = pre[-1]
        tr = [(t, p) for t, p in tr if ref / 3 < p < ref * 3]          # operaciones raras (un precio 77 veces): afuera
        out.append({"t0": t0, "ref": ref, "tr": tr, "kind": events[t0]["tipo"], "sym": f.stem.rsplit("-", 1)[0]})
    return sorted(out, key=lambda e: e["t0"])


def entry(tr, t_in):
    return next(((t, p) for t, p in tr if t >= t_in), None)


def run(tr, t_in, hold_s, trail=None):
    """Retorno neto entrando en la primera operación desde t_in. trail: fracción fija, o una función de los segundos
    desde la entrada (el "dinámico"). Sale por el stop de 10 %, por el que sigue al pico, o a los hold_s."""
    got = entry(tr, t_in)
    if got is None:
        return None
    t_e, p_e = got
    px, hi, end, last = p_e * (1 + SLIP), p_e, t_e + hold_s * 1000, None
    for t, p in tr:
        if t <= t_e:
            continue
        if t > end:
            break
        hi = max(hi, p)
        tf = trail((t - t_e) / 1000) if callable(trail) else trail
        if p <= px * (1 - STOP) or (tf and p <= hi * (1 - tf)):
            return p * (1 - SLIP) / px - 1 - COST
        last = p
    return None if last is None else last / px - 1 - COST


def dynamic(s):
    return 0.08 if s < 120 else 0.04


def half(tr, t_in):
    a, b = run(tr, t_in, 60), run(tr, t_in, RULE_S)
    return None if a is None or b is None else (a + b) / 2


VARIANTS = ([(f"fija {s // 60} min" if s >= 120 else f"fija {s} s", lambda tr, t, s=s: run(tr, t, s)) for s in FIXED_S]
            + [(f"sigue al pico {int(x * 100)} %, hasta {h // 60} min", lambda tr, t, x=x, h=h: run(tr, t, h, x))
               for h in (900, 3600) for x in (0.03, 0.05, 0.08)]
            + [("dinámico 8 % → 4 % a los 2 min, hasta 15 min", lambda tr, t: run(tr, t, 900, dynamic)),
               ("mitad a los 60 s + mitad a los 15 min", half)])
RULE = "fija 15 min"


def pct(x):
    return f"{x * 100:+6.2f}%"


def stats(xs):
    xs = [x for x in xs if x is not None]
    return (f"n {len(xs):2d} · mediana {pct(statistics.median(xs))} · media {pct(statistics.mean(xs))} · "
            f"sin el mejor {pct(statistics.mean(sorted(xs)[:-1]))} · positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% · "
            f"peor {min(xs) * 100:+6.1f}% · mejor {max(xs) * 100:+6.1f}%")


def account(xs):
    eq, peak, dd = 100.0, 100.0, 0.0
    for r in xs:
        eq *= 1 + r * X
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq, dd


def paired(xs, base, rng):
    """(en cuántos gana, diferencia media, rango del 90 % de la diferencia media por remuestreo)."""
    d = [x - b for x, b in zip(xs, base, strict=True) if x is not None and b is not None]
    means = sorted(statistics.mean(rng.choices(d, k=len(d))) for _ in range(BOOT))
    return sum(x > 0 for x in d), len(d), statistics.mean(d), means[int(BOOT * 0.05)], means[int(BOOT * 0.95)]


def closes(tr, t_from, t_to):
    """El último precio de cada segundo entre t_from y t_to: {segundo: precio}."""
    out = {}
    for t, p in tr:
        if t_from <= t <= t_to:
            out[t // 1000] = p
    return out


rows = load()
print(f"listados con operaciones: {len(rows)} (spot nuevo {sum(r['kind'] == 'listado_spot' for r in rows)}, "
      f"alta en wones {sum(r['kind'] == 'listado_krw' for r in rows)}), del "
      f"{time.strftime('%Y-%m-%d', time.gmtime(rows[0]['t0'] / 1000))} al "
      f"{time.strftime('%Y-%m-%d', time.gmtime(rows[-1]['t0'] / 1000))}")
print(f"entrada: Telegram a {TG_MAIN} s del sitio + {ORDER_S} s a la orden (Tokio), +0,1 % de deslizamiento; "
      "stop fijo de 10 % siempre; 0,2 % de costos\n")

t_in = {id(r): r["t0"] + (TG_MAIN + ORDER_S) * 1000 for r in rows}
res = {name: [fn(r["tr"], t_in[id(r)]) for r in rows] for name, fn in VARIANTS}
rng = random.Random(0)
print("== 1 y 3. Cada salida (a 1 s de Telegram) ==")
for name, _ in VARIANTS:
    eq, dd = account([x for x in res[name] if x is not None])
    line = f"{name:44} {stats(res[name])} · cuenta 100 → {eq:6.1f} (peor caída {dd * 100:4.1f}%)"
    if name != RULE:
        won, n, m, lo, hi = paired(res[name], res[RULE], rng)
        line += f"\n{'':44} contra la de hoy: gana en {won:2d} de {n} · diferencia media {pct(m)} (90 %: {pct(lo)} a {pct(hi)})"
    print(line)

print("\n== 2. El pico (cierre de cada segundo, desde la entrada a 1 s de Telegram) ==")
peaks = []
for r in rows:
    got = entry(r["tr"], t_in[id(r)])
    px = got[1] * (1 + SLIP)
    for horizon in (900, 3600):
        c = closes(r["tr"], got[0], got[0] + horizon * 1000)
        s, p = max(c.items(), key=lambda kv: kv[1])
        r[f"peak{horizon}"] = (s - r["t0"] / 1000, p / px - 1)
    c15 = closes(r["tr"], got[0], got[0] + RULE_S * 1000)
    r["entry_move"] = got[1] / r["ref"] - 1
    r["giveback"] = 1 - c15[max(c15)] / max(c15.values())
    peaks.append(r)
for horizon in (900, 3600):
    ts = sorted(r[f"peak{horizon}"][0] for r in peaks)
    mfe = [r[f"peak{horizon}"][1] for r in peaks]
    q = statistics.quantiles(ts, n=4)
    print(f"en los primeros {horizon // 60} min: el techo llega a los {ts[len(ts) // 2]:.0f} s del aviso (mediana; "
          f"cuartos {q[0]:.0f} s y {q[2]:.0f} s) · sube desde la entrada: mediana {pct(statistics.median(mfe))}, "
          f"media {pct(statistics.mean(mfe))}")
    for lo, hi in ((0, 10), (10, 30), (30, 60), (60, 120), (120, 300), (300, 900), (900, 3600)):
        if lo >= horizon:
            break
        k = sum(lo <= t < hi for t in ts)
        print(f"    techo entre {lo:4d} y {hi:4d} s: {k:2d} listados {'#' * k}")
gb = [r["giveback"] for r in peaks]
print(f"a los 15 min devolvió desde el techo: mediana {statistics.median(gb) * 100:.1f}%, media "
      f"{statistics.mean(gb) * 100:.1f}%")

print("\n== Listado por listado (a 1 s de Telegram; retornos netos con el stop de 10 %) ==")
print(f"{'fecha':16} {'moneda':12} {'tipo':5} {'entra':>7} {'30 s':>7} {'60 s':>7} {'2 min':>7} {'5 min':>7} "
      f"{'15 min':>7} {'techo a':>8} {'techo':>7} {'devolvió':>8}")
for i, r in enumerate(rows):
    cells = " ".join(f"{res[n][i] * 100:+6.1f}%" if res[n][i] is not None else "    -  "
                     for n in ("fija 30 s", "fija 60 s", "fija 2 min", "fija 5 min", RULE))
    print(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['t0'] / 1000)):16} {r['sym']:12} "
          f"{'spot' if r['kind'] == 'listado_spot' else 'KRW':5} {r['entry_move'] * 100:+6.1f}% {cells} "
          f"{r['peak900'][0]:6.0f} s {r['peak900'][1] * 100:+6.1f}% {r['giveback'] * 100:7.1f}%")

print("\n== 4. ¿Se sostiene? Media (mediana) de cada mitad de los listados, por fecha ==")
n2 = len(rows) // 2
print(f"primera mitad: {n2} listados hasta {time.strftime('%Y-%m-%d', time.gmtime(rows[n2 - 1]['t0'] / 1000))}; "
      f"segunda: {len(rows) - n2}")
for name, _ in VARIANTS:
    a = [x for x in res[name][:n2] if x is not None]
    b = [x for x in res[name][n2:] if x is not None]
    print(f"  {name:44} 1.ª {pct(statistics.mean(a))} ({pct(statistics.median(a))}) · "
          f"2.ª {pct(statistics.mean(b))} ({pct(statistics.median(b))})")

print("\n== Con otras demoras de Telegram (media · mediana · positivas) ==")
for tg in TG_S:
    print(f"Telegram a {tg} s:")
    for name, fn in VARIANTS:
        if name not in ("fija 45 s", "fija 60 s", "fija 5 min", RULE, "fija 60 min", "sigue al pico 5 %, hasta 15 min",
                        "sigue al pico 8 %, hasta 60 min", "mitad a los 60 s + mitad a los 15 min"):
            continue
        xs = [x for r in rows if (x := fn(r["tr"], r["t0"] + (tg + ORDER_S) * 1000)) is not None]
        print(f"  {name:44} media {pct(statistics.mean(xs))} · mediana {pct(statistics.median(xs))} · "
              f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}%")
