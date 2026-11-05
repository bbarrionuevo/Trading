"""¿Cuánto conviene poner en cada operación? Con las 60 operaciones medidas de "resultados con impulso" (umbral 0,5 %,
stop 5 %, salida a los 30 min, 0,15 % de costos), el saldo se simula operación tras operación con distintos tamaños de
posición (fracción del saldo; más de 1 = apalancado). Además: Kelly (el tamaño que más hace crecer el saldo a la
larga), remuestreo de 10.000 años de 80 operaciones y cuánto pasó el precio más allá del stop en el minuto en que
saltó (riesgo de que el stop se llene peor)."""
import asyncio
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime

import aiohttp

sys.stdout.reconfigure(encoding="utf-8")

FAPI = "https://fapi.binance.com"
MIN = 60_000
COST = 0.0015
THR, STOP, HOLD = 0.005, 0.05, 30


def load_events() -> list:
    with open(os.path.join(os.environ["LOCALAPPDATA"], "TRADING", "datos", "estudios", "resultados-tradfi.json"),
              encoding="utf-8") as f:
        return json.load(f)["eventos"]


async def trades():
    events = load_events()
    out = []
    async with aiohttp.ClientSession() as s:
        for e in events:
            t0 = datetime.fromisoformat(e["t0"].replace("Z", "+00:00")).timestamp()
            m0 = int(t0 * 1000) // MIN * MIN
            async with s.get(FAPI + "/fapi/v1/klines", params={"symbol": f"{e['tk']}USDT", "interval": "1m",
                                                                "startTime": m0 - 2 * MIN, "limit": 66}) as r:
                k = await r.json()
            bars = {int(x[0]): (float(x[2]), float(x[3]), float(x[4])) for x in k} if isinstance(k, list) else {}
            if not ((m0 - MIN) in bars and all((m0 + i * MIN) in bars for i in range(0, 62))):
                continue
            pre, path = bars[m0 - MIN][2], [bars[m0 + i * MIN] for i in range(0, 62)]
            entry = path[1][2]
            mv = entry / pre - 1
            if abs(mv) < THR:
                continue
            side = 1 if mv > 0 else -1
            res, beyond = None, 0.0
            for h, low, _c in path[2:HOLD + 1]:
                ext = low if side > 0 else h
                if (ext / entry - 1) * side <= -STOP:
                    res, beyond = -STOP, (ext / entry - 1) * side + STOP
                    break
            if res is None:
                res = (path[HOLD][2] / entry - 1) * side
            out.append((t0, res - COST, beyond))
    return sorted(out)


def path_stats(rs, f):
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= max(1e-9, 1 + f * r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return eq, mdd


async def main():
    t = await trades()
    rs = [r for _, r, _ in t]
    print(f"operaciones: {len(rs)}, media {statistics.mean(rs) * 100:+.2f} %, desvío {statistics.pstdev(rs) * 100:.2f} %")
    stops = [b for _, r, b in t if b or r <= -STOP]
    print(f"stops tocados: {len(stops)}; más allá del stop en ese minuto: peor {min(stops, default=0) * 100:+.2f} %")
    best = max((sum(math.log(max(1e-9, 1 + f / 100 * r)) for r in rs), f / 100) for f in range(5, 2001, 5))
    se = statistics.pstdev(rs) / math.sqrt(len(rs))
    shrunk = [r - se for r in rs]                                    # el acierto medido puede estar inflado
    best_s = max((sum(math.log(max(1e-9, 1 + f / 100 * r)) for r in shrunk), f / 100) for f in range(5, 2001, 5))
    print(f"Kelly: {best[1]:.2f}x el saldo; con la ganancia media rebajada 1 error estándar: {best_s[1]:.2f}x")
    random.seed(7)
    print("tamaño | riesgo al stop | saldo final (60 ops) | caída máx | año simulado: mediana, peor 5 %, "
          "P(caída ≥ 8 %), P(perder ≥ 30 %)")
    for f in (0.2, 0.3, 0.4, 0.6, 1.0, 2.0, 3.0):
        eq, mdd = path_stats(rs, f)
        sims = []
        for _ in range(10_000):
            year = [random.choice(rs) for _ in range(80)]
            e, m = path_stats(year, f)
            sims.append((e, m))
        es = sorted(e for e, _ in sims)
        p8 = sum(m <= -0.08 for _, m in sims) / len(sims)
        p30 = sum(e <= 0.70 for e, _ in sims) / len(sims)
        print(f"{f:4.1f}x | {f * STOP * 100:4.1f} % | {eq:5.2f}x | {mdd * 100:5.1f} % | "
              f"{(es[5000] - 1) * 100:+6.0f} %, {(es[500] - 1) * 100:+5.0f} %, {p8 * 100:3.0f} %, {p30 * 100:3.0f} %")


asyncio.run(main())
