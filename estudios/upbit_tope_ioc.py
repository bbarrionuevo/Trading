"""Listados de Upbit con dinero real: ¿se llena la orden? La entrada es una orden límite IOC con tope de precio (REGLAS
D-2: nunca a mercado sin tope; hoy 0,5 % sobre el mejor precio de venta del libro, `max_slippage_bps: 50`). En el salto
el libro se vacía: si el precio sube más que el tope entre que se lee el libro y llega la orden, no se llena y no se
entra. ¿Cuántas veces pasa, y convendría un tope más ancho?

Con las ventanas de `estudios/upbit_segundos.py` (31 listados con perpetuo en Binance, 180 días) y las demoras de Tokio:
Telegram 0,5 / 1 / 2 s después del sitio, el libro leído a los +25 ms y la orden en el exchange a los +50 ms. El archivo
público tiene operaciones y no el libro: el libro es el último precio operado antes de leerlo; la orden se llena si en
los 20 ms desde que llega hay una operación a ese tope o menos (si no hubo ninguna, si la siguiente lo está), a ese
precio. La salida es la de la regla (stop fijo de 10 %, 15 min) con 0,2 % de costos.

Uso: python estudios/upbit_tope_ioc.py <carpeta con listados-upbit.json y upbit_segundos/>
"""
import json
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1])
COST, STOP, HOLD_S = 0.002, 0.10, 900
TG_S = (0.5, 1.0, 2.0)                       # el mensaje de Telegram después del sitio
BOOK_S, ORDER_S, MATCH_S = 0.025, 0.05, 0.02  # el libro leído, la orden en el exchange, lo que tarda en cruzarse
CAPS = (0.005, 0.01, 0.02, 0.03, 0.05, 0.10)


def load():
    events = {int(float(e["t"]) * 1000): e for e in json.loads((D / "listados-upbit.json").read_text(encoding="utf-8"))
              ["eventos"]}
    out = []
    for f in sorted((D / "upbit_segundos").glob("*.json")):
        t0 = int(f.stem.rsplit("-", 1)[1])
        tr = json.loads(f.read_text(encoding="utf-8"))
        pre = [p for t, p in tr if t < t0]
        if not pre or t0 not in events:
            continue
        tr = [(t, p) for t, p in tr if pre[-1] / 3 < p < pre[-1] * 3]   # operaciones raras (un precio 77 veces): afuera
        out.append((t0, tr))
    return out


def exit_return(tr, t_in, px):
    """Retorno neto desde el precio de entrada px: stop fijo de 10 % o salida a los 15 min."""
    end, last = t_in + HOLD_S * 1000, None
    for t, p in tr:
        if t <= t_in:
            continue
        if t > end:
            break
        if p <= px * (1 - STOP):
            return (1 - STOP) * (1 - 0.001) - 1 - COST
        last = p
    return None if last is None else last / px - 1 - COST


def attempt(tr, t0, tg, cap):
    """(se llenó, retorno) de la orden IOC con tope `cap` sobre el libro leído a los tg + 25 ms."""
    t_book, t_ord = t0 + (tg + BOOK_S) * 1000, t0 + (tg + ORDER_S) * 1000
    ref = next((p for t, p in reversed(tr) if t <= t_book), None)
    if ref is None:
        return False, None
    limit = ref * (1 + cap)
    fill = next(((t, p) for t, p in tr if t_ord <= t <= t_ord + MATCH_S * 1000 and p <= limit), None)
    if fill is None:
        later = next(((t, p) for t, p in tr if t >= t_ord), None)
        if later is None or later[1] > limit:
            return False, None
        fill = later
    return True, exit_return(tr, fill[0], fill[1])


def stats(xs):
    if not xs:
        return "n 0"
    return (f"mediana {statistics.median(xs) * 100:+6.2f}% · media {statistics.mean(xs) * 100:+6.2f}% · "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}%")


rows = load()
print(f"listados con operaciones: {len(rows)}\n")
for tg in TG_S:
    print(f"Telegram a {tg} s del sitio (la orden en el exchange a {tg + ORDER_S:.2f} s):")
    for cap in CAPS:
        got = [attempt(tr, t0, tg, cap) for t0, tr in rows]
        filled = [r for ok, r in got if ok and r is not None]
        per_event = statistics.mean([r if ok and r is not None else 0.0 for ok, r in got])
        print(f"  tope {cap * 100:4.1f} %: se llena en {sum(ok for ok, _ in got):2d} de {len(got)} · las llenas: "
              f"{stats(filled)} · por listado (las no llenas = 0): {per_event * 100:+6.2f}%")
    print()
