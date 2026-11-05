"""Upbit: fin del soporte ("거래지원 종료") y advertencia ("유의 종목 지정") — ¿cuánto cae el perpetuo de Binance?
Toma los avisos del estudio de Upbit (datos/estudios/listados-upbit.json en la PC), busca el perpetuo en USDT de cada
moneda (también los ya cerrados) y baja velas de 1 min (16 h), de 1 h (7 días) y el funding, en el mismo formato que
estudios/deslistados_binance.py; el análisis segundo a segundo lo hace estudios/deslistados_segundos.py.
Solo fapi.binance.com (nunca el sitio de anuncios). Uso: python estudios/upbit_negativos.py <carpeta>"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(sys.argv[1])
FAPI = "https://fapi.binance.com"
SRC = Path(os.environ["LOCALAPPDATA"]) / "TRADING" / "datos" / "estudios" / "listados-upbit.json"
KINDS = {"deslistado", "alerta"}


async def get(s, path, **params):
    async with s.get(FAPI + path, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
        used = int(r.headers.get("X-MBX-USED-WEIGHT-1M", 0))
        data = await r.json() if r.status == 200 else None
    await asyncio.sleep(0.2 if used < 900 else 30)
    return data


async def main():
    src = json.loads(SRC.read_text(encoding="utf-8"))["eventos"]
    async with aiohttp.ClientSession() as s:
        info = await get(s, "/fapi/v1/exchangeInfo")
        perps = {x["symbol"] for x in info["symbols"] if x.get("quoteAsset") == "USDT"
                 and x.get("contractType") == "PERPETUAL"}
        events, last_f = [], 0.0
        for e in src:
            if e.get("tipo") not in KINDS:
                continue
            tk = str(e.get("ticker") or "").upper()
            sym = next((c for c in (f"{tk}USDT", f"1000{tk}USDT") if c in perps), None)
            t0 = int(float(e["t"]) * 1000) if float(e["t"]) < 1e12 else int(e["t"])
            if sym is None:
                continue
            m1 = await get(s, "/fapi/v1/klines", symbol=sym, interval="1m", startTime=t0 - 120_000, limit=1000)
            h1 = await get(s, "/fapi/v1/klines", symbol=sym, interval="1h", startTime=t0, limit=170)
            wait = last_f + 1.3 - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            last_f = time.monotonic()
            fr = await get(s, "/fapi/v1/fundingRate", symbol=sym, startTime=t0, endTime=t0 + 8 * 86_400_000, limit=100)
            if not m1:
                continue
            events.append({"title": f"{e['tipo']}: {e.get('titulo', '')[:80]}", "tipo": e["tipo"], "symbol": sym,
                           "t0": t0,
                           "m1": [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])] for b in m1],
                           "h1": [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])]
                                  for b in (h1 or [])],
                           "funding": [(int(x["fundingTime"]), float(x["fundingRate"])) for x in (fr or [])]})
    (OUT / "upbit-negativos.json").write_text(json.dumps(events), encoding="utf-8")
    print("avisos de Upbit con perpetuo:", len(events), {k: sum(1 for x in events if x["tipo"] == k) for k in KINDS})


asyncio.run(main())
