"""Datos públicos de Binance Futures para dos estudios (solo lectura, sin claves):
1. Lanzamientos de perpetuos: hora de alta (onboardDate), velas de 1 min de las primeras 16 h, de 1 h de 7 días y
   el funding de esos días.
2. Tendencia: velas diarias desde 2019 y funding de los perpetuos de cripto con historia larga.
Respeta el límite de peso por IP (lo comparte con el agente de la PC): frena si el peso usado pasa de 900/min."""
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

BASE = "https://fapi.binance.com"
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
SINCE_LAUNCH = 1672531200000          # 1-ene-2023
DAY = 86_400_000


class Api:
    def __init__(self, s):
        self.s = s
        self.used = 0
        self.last_funding = 0.0

    async def get(self, path, **params):
        for _ in range(6):
            if self.used > 900:
                await asyncio.sleep(61 - time.time() % 60)
                self.used = 0
            if path == "/fapi/v1/fundingRate":           # 500 cada 5 min por IP: uno cada 1,3 s
                wait = self.last_funding + 1.3 - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self.last_funding = time.monotonic()
            async with self.s.get(BASE + path, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                self.used = int(r.headers.get("X-MBX-USED-WEIGHT-1M", self.used))
                if r.status in (418, 429):
                    print("límite", r.status, path, flush=True)
                    await asyncio.sleep(90)
                    continue
                if r.status != 200:
                    return None
                return await r.json()
        return None


async def funding(api, sym, start, end):
    out, t = [], start
    while t < end:
        rows = await api.get("/fapi/v1/fundingRate", symbol=sym, startTime=t, endTime=end, limit=1000)
        if not rows:
            break
        out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in rows]
        if len(rows) < 1000:
            break
        t = int(rows[-1]["fundingTime"]) + 1
    return out


async def main():
    async with aiohttp.ClientSession() as s:
        api = Api(s)
        info = await api.get("/fapi/v1/exchangeInfo")
        perps = [x for x in info["symbols"] if x.get("contractType") == "PERPETUAL" and x.get("quoteAsset") == "USDT"
                 and x.get("underlyingType") == "COIN"]
        print("perpetuos de cripto:", len(perps), flush=True)
        # 2. Tendencia: con al menos 3 años de historia
        old = [x["symbol"] for x in perps if x["onboardDate"] < 1704067200000 - 2 * 365 * DAY]   # antes de 2022
        trend = {}
        now = int(time.time() * 1000)
        for sym in old:
            bars, t = [], 1567296000000                                       # 1-sep-2019
            while t < now:
                k = await api.get("/fapi/v1/klines", symbol=sym, interval="1d", startTime=t, limit=1000)
                if not k:
                    break
                bars += [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])] for b in k]
                if len(k) < 1000:
                    break
                t = int(k[-1][0]) + DAY
            fr = await funding(api, sym, bars[0][0] if bars else now, now) if bars else []
            trend[sym] = {"velas": bars, "funding": fr}
        (OUT / "tendencia.json").write_text(json.dumps(trend), encoding="utf-8")
        print("tendencia:", len(trend), "contratos", flush=True)
        # 1. Lanzamientos desde 2023
        news = sorted((x for x in perps if x["onboardDate"] >= SINCE_LAUNCH and x["onboardDate"] < now - 8 * DAY),
                      key=lambda x: x["onboardDate"])
        launches = []
        for x in news:
            sym, t0 = x["symbol"], int(x["onboardDate"])
            m1 = await api.get("/fapi/v1/klines", symbol=sym, interval="1m", startTime=t0, limit=1000)
            h1 = await api.get("/fapi/v1/klines", symbol=sym, interval="1h", startTime=t0, limit=170)
            fr = await funding(api, sym, t0, t0 + 8 * DAY)
            if not m1:
                continue
            launches.append({"symbol": sym, "t0": t0,
                             "m1": [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])]
                                    for b in m1],
                             "h1": [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])]
                                    for b in (h1 or [])], "funding": fr})
            if len(launches) % 50 == 0:
                print("lanzamientos:", len(launches), flush=True)
        (OUT / "lanzamientos.json").write_text(json.dumps(launches), encoding="utf-8")
        print("lanzamientos:", len(launches), "listo", flush=True)


asyncio.run(main())
