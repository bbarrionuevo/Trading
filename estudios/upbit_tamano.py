"""¿Cuánto poner en los listados de Upbit al instante? ¿3 %, más, apalancamiento? (pedido del dueño, 5-nov: "arriesga el
3% o más si beneficia"; "habiendo mejorado tanto los tiempos de respuesta chequea si sirve más apalancamiento").

Con los retornos de cada listado (estudios/upbit_ia_o_rapido.py: entrada al instante, stop fijo de 10 %, 15 min,
costos), para cada tamaño (veces el saldo): la cuenta de 100 USDT con los 31 en su orden real y con 10.000 órdenes al
azar (con reposición): mediana y peor 5 % del final, chance de tocar el freno de 8 % (halt, A-4) y de perder más de 2 %
en una operación (el freno del día). Además, el Kelly entero (el que maximiza el crecimiento) y el ¼ que usa el sistema.
Uso: python estudios/upbit_tamano.py <carpeta con listados-upbit.json y upbit_segundos/>
"""
import importlib.util
import math
import random
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
spec = importlib.util.spec_from_file_location("rap", Path(__file__).with_name("upbit_ia_o_rapido.py"))
rap = importlib.util.module_from_spec(spec)
argv, sys.argv = sys.argv, [sys.argv[0], sys.argv[1]]
spec.loader.exec_module(rap)                       # imprime su propio resumen: se separa con una línea
sys.argv = argv
print("\n" + "=" * 100)
SIZES = (0.10, 0.20, 0.30, 0.50, 0.60, 1.0, 2.0, 3.0)
PATHS = 10_000


def path(rs, x):
    eq, peak, dd = 100.0, 100.0, 0.0
    for r in rs:
        eq *= max(1 + r * x, 0.0)
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq, dd


for tg in (1.0, 2.0):
    rs = [x for t0, _, tr, _ in rap.load() if (x := rap.trade(tr, t0 + (tg + rap.ORDER_S) * 1000)) is not None]
    kelly = max((k / 100 for k in range(1, 1001)), key=lambda f: sum(math.log(max(1 + f * r, 1e-9)) for r in rs))
    print(f"\nTelegram a {tg:.0f} s: {len(rs)} listados · retorno por operación mediana {statistics.median(rs) * 100:+.2f}% "
          f"media {statistics.mean(rs) * 100:+.2f}% · peor {min(rs) * 100:+.1f}% · Kelly entero {kelly:.2f} veces el saldo "
          f"(¼ = {kelly / 4:.2f})")
    rng = random.Random(7)
    sims = [[rng.choice(rs) for _ in rs] for _ in range(PATHS)]
    print(f"  {'tamaño':>8} {'riesgo al stop':>15} {'orden real':>12} {'mediana':>9} {'peor 5 %':>9} {'halt 8 %':>9} "
          f"{'pierde > 2 % en una':>20}")
    for x in SIZES:
        real_eq, _ = path(rs, x)
        res = [path(s, x) for s in sims]
        finals = sorted(r[0] for r in res)
        halt = sum(r[1] >= 0.08 for r in res) / PATHS
        big = sum(min(s) * x <= -0.02 for s in sims) / PATHS
        print(f"  {x:7.2f}x {x * 10.3:13.1f} % {real_eq:11.1f} {statistics.median(finals):9.1f} "
              f"{finals[PATHS // 20]:9.1f} {halt * 100:8.1f}% {big * 100:19.1f}%")
