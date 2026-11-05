"""¿La regla de impulso de los resultados sirve con los 6-K (empresas extranjeras: TSMC, Alibaba, Sony...)? Para cada empresa con perpetuo TradFi en Binance y
ticker en la SEC: todos sus 6-K desde febrero de 2026 (el 6-K no trae ítems: resultados y otros avisos mezclados), con la hora de
aceptación de la SEC, y las velas de 1 minuto del perpetuo. Regla igual a la de resultados: a los 2 minutos, si se
movió ≥ 0,5 % contra el cierre de antes, entrar en esa dirección; stop fijo de 5 %, salida a los 30 min, 0,15 % de
costos. Comparación: la misma regla en horas al azar sin 8-K. Datos públicos (SEC con el User-Agent del .env).
Uso: python estudios/eventos_6k.py <carpeta de salida>"""
import asyncio
import json
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

from tradingagent import paths

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(sys.argv[1])
FAPI = "https://fapi.binance.com"
SINCE = datetime(2026, 2, 1, tzinfo=UTC).timestamp()
MIN = 60_000
COST, THR, STOP, HOLD = 0.0015, 0.005, 0.05, 30


async def klines(s, sym, m0):
    async with s.get(FAPI + "/fapi/v1/klines", params={"symbol": sym, "interval": "1m", "startTime": m0 - 2 * MIN,
                                                        "limit": 66}) as r:
        used = int(r.headers.get("X-MBX-USED-WEIGHT-1M", 0))
        k = await r.json() if r.status == 200 else []
    await asyncio.sleep(0.12 if used < 900 else 30)
    return {int(x[0]): (float(x[2]), float(x[3]), float(x[4])) for x in k} if isinstance(k, list) else {}


def rule(bars, m0):
    """(resultado de la operación, movimiento a los 2 min) o None si faltan velas o no hay impulso."""
    if (m0 - MIN) not in bars or not all((m0 + i * MIN) in bars for i in range(0, HOLD + 2)):
        return None
    pre, path = bars[m0 - MIN][2], [bars[m0 + i * MIN] for i in range(0, HOLD + 2)]
    entry = path[1][2]
    mv = entry / pre - 1
    if abs(mv) < THR:
        return 0.0, mv, False
    side = 1 if mv > 0 else -1
    for h, lo, _c in path[2:HOLD + 1]:
        if ((lo if side > 0 else h) / entry - 1) * side <= -STOP:
            return -STOP - COST, mv, True
    return (path[HOLD][2] / entry - 1) * side - COST, mv, True


async def main():
    paths.load_env()
    ua = {"User-Agent": os.environ["SEC_USER_AGENT"]}
    async with aiohttp.ClientSession() as s:
        async with s.get("https://www.sec.gov/files/company_tickers.json", headers=ua) as r:
            table = {v["ticker"]: int(v["cik_str"]) for v in (await r.json()).values()}
        async with s.get(FAPI + "/fapi/v1/exchangeInfo") as r:
            perps = {x["baseAsset"]: x["symbol"] for x in (await r.json())["symbols"]
                     if x.get("contractType") == "TRADIFI_PERPETUAL" and x.get("status") == "TRADING"}
        events = []
        for tk, sym in sorted(perps.items()):
            if tk not in table:
                continue
            await asyncio.sleep(0.15)                               # la SEC pide ≤ 10 pedidos por segundo
            async with s.get(f"https://data.sec.gov/submissions/CIK{table[tk]:010d}.json", headers=ua) as r:
                if r.status != 200:
                    continue
                sub = (await r.json())["filings"]["recent"]
            for form, items, acc in zip(sub["form"], sub["items"], sub["acceptanceDateTime"], strict=False):
                if form != "6-K":
                    continue
                t0 = datetime.fromisoformat(acc.replace("Z", "+00:00")).timestamp()
                if t0 >= SINCE:
                    events.append((tk, sym, int(t0 * 1000) // MIN * MIN, items or ""))
        print("6-K:", len(events), "de", len({e[0] for e in events}), "empresas", flush=True)
        res, rnd = [], []
        for tk, sym, m0, items in events:
            got = rule(await klines(s, sym, m0), m0)
            if got:
                res.append((tk, items, *got))
            m1 = m0 + random.choice((-1, 1)) * random.randint(2, 20) * 3_600_000      # misma empresa, otra hora
            got = rule(await klines(s, sym, m1 // MIN * MIN), m1 // MIN * MIN)
            if got:
                rnd.append(got)
    (OUT / "eventos_6k.json").write_text(json.dumps({"res": res, "azar": rnd}), encoding="utf-8")

    def show(name, rows):
        trig = [r for r in rows if r[-1]]
        xs = [r[-3] for r in trig]
        if len(xs) < 8:
            print(f"{name:34s} con impulso {len(xs)} de {len(rows)}")
            return
        print(f"{name:34s} con impulso {len(xs):3d} de {len(rows):4d} | mediana {statistics.median(xs) * 100:+.2f}% "
              f"media {statistics.mean(xs) * 100:+.2f}% positivas {sum(x > 0 for x in xs) * 100 / len(xs):.0f}%")

    show("todos los 6-K", res)
    show("horas al azar (sin 8-K)", rnd)
    by_tk = defaultdict(list)
    for r in res:
        by_tk[r[0]].append(r)
    print("empresas:", ", ".join(f"{k} {len(v)}" for k, v in sorted(by_tk.items(), key=lambda kv: -len(kv[1]))[:20]))
    big = [r for r in res if r[-1] and abs(r[-2]) >= 0.01]
    show("6-K con impulso >= 1 %", [(*r[:-1], True) for r in big] if big else [])


asyncio.run(main())
