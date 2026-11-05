"""Listados de Upbit desde Tokio: ¿entrar al instante (sin la IA) o esperar a que la IA confirme (y poner más)?
Pedido del dueño (5-nov): "probar desde Tokio qué es mejor, si invertir automáticamente que llega la noticia por
telegram de upbit (sin pasar por la ia para ser más rápidos) y pagar las comisiones por los falsos positivos.. o si
pasar todas las noticias por la [IA] e invertir solamente cuando tengamos la confirmación de la IA e inclusive utilizar
apalancamiento si tenemos la certeza".

Con las ventanas de cada operación que dejó `estudios/upbit_segundos.py` (31 listados con perpetuo en Binance, 180
días) y las demoras medidas desde Tokio: Telegram 0-2 s después del sitio (mediana 1 s), del aviso a la orden ~0,05 s,
la IA 1,3-2,5 s (`llm-check` desde Tokio: 1,8 s). A: entra al llegar el aviso. B: entra cuando la IA confirma. Las
dos con la salida de la regla (stop fijo de 10 %, 15 min), +0,1 % de deslizamiento y 0,2 % de costos.
Uso: python estudios/upbit_ia_o_rapido.py <carpeta con listados-upbit.json y upbit_segundos/>
"""
import json
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path(sys.argv[1])
COST, SLIP, STOP, HOLD_S = 0.002, 0.001, 0.10, 900
TG_S = (0.5, 1.0, 2.0)                       # el mensaje de Telegram después del sitio
AI_S = (1.3, 1.8, 2.5)                       # lo que tarda la IA
ORDER_S = 0.05                               # del aviso a la orden, desde Tokio


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
        ref = pre[-1]
        tr = [(t, p) for t, p in tr if ref / 3 < p < ref * 3]          # operaciones raras (un precio 77 veces): afuera
        out.append((t0, ref, tr, events[t0]["tipo"]))
    return out


def trade(tr, t_in):
    """Retorno neto entrando en la primera operación desde t_in: stop fijo o salida a los 15 min."""
    entry = next(((t, p) for t, p in tr if t >= t_in), None)
    if entry is None:
        return None
    px = entry[1] * (1 + SLIP)
    end = entry[0] + HOLD_S * 1000
    last = None
    for t, p in tr:
        if t <= entry[0]:
            continue
        if t > end:
            break
        if p <= px * (1 - STOP):
            return (1 - STOP) * (1 - SLIP) - 1 - COST                    # el stop, con deslizamiento
        last = p
    if last is None:
        return None
    return last / px - 1 - COST


def stats(xs):
    if not xs:
        return "n 0"
    return (f"n {len(xs):2d} · mediana {statistics.median(xs) * 100:+5.2f}% · media {statistics.mean(xs) * 100:+5.2f}% · "
            f"positivas {sum(x > 0 for x in xs) * 100 / len(xs):3.0f}% · peor {min(xs) * 100:+5.1f}%")


def account(xs, x_equity):
    """100 USDT de mentira, una operación tras otra con `x_equity` veces el saldo: final y peor caída."""
    eq, peak, dd = 100.0, 100.0, 0.0
    for r in xs:
        eq *= 1 + r * x_equity
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq, dd


rows = load()
print(f"listados con operaciones: {len(rows)} (spot nuevo {sum(r[3] == 'listado_spot' for r in rows)}, "
      f"alta en wones {sum(r[3] == 'listado_krw' for r in rows)})\n")
for tg in TG_S:
    a = [x for t0, _, tr, _ in rows if (x := trade(tr, t0 + (tg + ORDER_S) * 1000)) is not None]
    print(f"Telegram a {tg:.1f} s del sitio:")
    print(f"  A · al instante ({tg + ORDER_S:.2f} s): {stats(a)}")
    for ai in AI_S:
        b = [x for t0, _, tr, _ in rows if (x := trade(tr, t0 + (tg + ORDER_S + ai) * 1000)) is not None]
        print(f"  B · con la IA ({ai:.1f} s, entra a {tg + ORDER_S + ai:.2f} s): {stats(b)}")
    print()
a = [x for t0, _, tr, _ in rows if (x := trade(tr, t0 + (1.0 + ORDER_S) * 1000)) is not None]
b = [x for t0, _, tr, _ in rows if (x := trade(tr, t0 + (1.0 + ORDER_S + 1.8) * 1000)) is not None]
print("En la cuenta de 100 USDT de mentira (31 listados en fila, Telegram a 1 s, IA a 1,8 s):")
for name, xs, size in (("A al instante, 0,10 veces el saldo (1 % al stop, la regla)", a, 0.10),
                       ("A al instante, 0,30 veces (3 % al stop, el máximo del sobre)", a, 0.30),
                       ("B con la IA, 0,10 veces", b, 0.10),
                       ("B con la IA, 0,30 veces (\"más si hay certeza\")", b, 0.30)):
    eq, dd = account(xs, size)
    print(f"  {name}: termina en {eq:6.1f} USDT · peor caída {dd * 100:4.1f} %")
