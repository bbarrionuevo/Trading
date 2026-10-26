"""Robustez del market making: 14 días, variantes de stop/timeout, divide en 2 mitades.

Un resultado cuenta solo si es positivo en AMBAS mitades (evita suerte de muestra).
python -m nexus.research_maker_robust OUT.md SYM1,SYM2
"""
import sys

from .backtest_trading import history
from .research_maker import simulate


def main(out, syms):
    L = ["# Robustez market making (1m, 14 días, dos mitades de 7 días)\n",
         "| par | medio spread | maker | stop % | timeout min | PnL mitad 1 % | PnL mitad 2 % | ¿robusto? |",
         "|---|---|---|---|---|---|---|---|"]
    robust = 0
    for s in syms:
        try:
            k = history(s, "1m", 20160)
        except Exception as e:
            L.append(f"| {s} | error {str(e)[:50]} |||||||")
            continue
        a, b = k[:len(k) // 2], k[len(k) // 2:]
        for hs in (0.15, 0.25, 0.4):
            for mk in (0.0, 0.1):
                for stop, to in ((1.0, 60), (3.0, 600), (100.0, 10 ** 9)):
                    r1 = simulate(a, hs, mk, 0.1, stop, to)["pnl_pct"]
                    r2 = simulate(b, hs, mk, 0.1, stop, to)["pnl_pct"]
                    ok = r1 > 0 and r2 > 0
                    robust += ok
                    L.append(f"| {s} | {hs} | {mk} | {stop if stop < 100 else 'sin'} | "
                             f"{to if to < 10 ** 9 else 'sin'} | {r1} | {r2} | {'SÍ' if ok else 'no'} |")
    L.insert(1, f"**Combinaciones robustas: {robust}**\n")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2].split(","))
