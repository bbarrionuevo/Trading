"""Deslistados de Binance: ¿cuánto cae el perpetuo después del aviso? Baja los avisos de la categoría "Delisting" (161)
del CMS público de Binance con su hora, saca las monedas del título y, para las que tienen perpetuo en USDT (también
los ya cerrados), las velas de 1 min de las primeras 16 h, las de 1 h de 7 días y el funding. Solo lectura, sin
claves, despacio (el mismo sitio lo lee el agente cada 3 s). Uso: python estudios/deslistados_binance.py <carpeta>"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import aiohttp

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
FAPI = "https://fapi.binance.com"
DELIST = re.compile(r"\bWill Delist\b(.*?)(?:\bon\b \d{4}-\d{2}-\d{2}|$)", re.I)
TOKEN = re.compile(r"\b[A-Z0-9]{2,12}\b")
SKIP = {"BINANCE", "USDT", "USD", "USDC", "FDUSD", "TRY", "EUR", "BTC", "ETH", "BNB", "AND", "THE", "ON", "WILL",
        "DELIST", "SPOT", "MARGIN", "FUTURES", "PERPETUAL", "CONTRACTS", "UTC", "SOME", "PAIRS", "TRADING"}


async def get(s, url, **params):
    for _ in range(4):
        async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status in (418, 429):
                print("límite", r.status, url, flush=True)
                await asyncio.sleep(120)
                continue
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    return None


async def main():
    async with aiohttp.ClientSession(headers={"Accept": "application/json"}) as s:
        arts = []
        for page in range(1, 41):
            d = await get(s, CMS, type=1, catalogId=161, pageNo=page, pageSize=20)
            items = (((d or {}).get("data") or {}).get("catalogs") or [{}])[0].get("articles") or []
            if not items:
                break
            arts += [{"title": a.get("title", ""), "t0": int(a.get("releaseDate") or 0)} for a in items]
            await asyncio.sleep(2.0)
        print("avisos:", len(arts), "desde", time.strftime("%Y-%m-%d", time.gmtime(min(a["t0"] for a in arts) / 1000)),
              flush=True)
        info = await get(s, FAPI + "/fapi/v1/exchangeInfo")
        perps = {x["symbol"]: x for x in info["symbols"] if x.get("quoteAsset") == "USDT"
                 and x.get("contractType") == "PERPETUAL"}
        events, last_f = [], 0.0
        for a in arts:
            m = DELIST.search(a["title"])
            if not m or "Futures Will Delist" in a["title"]:
                continue
            for tk in TOKEN.findall(m.group(1)):
                if tk in SKIP:
                    continue
                sym = next((c for c in (f"{tk}USDT", f"1000{tk}USDT") if c in perps), None)
                if sym is None:
                    continue
                t0 = a["t0"]
                m1 = await get(s, FAPI + "/fapi/v1/klines", symbol=sym, interval="1m", startTime=t0 - 120_000, limit=1000)
                h1 = await get(s, FAPI + "/fapi/v1/klines", symbol=sym, interval="1h", startTime=t0, limit=170)
                wait = last_f + 1.3 - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                last_f = time.monotonic()
                fr = await get(s, FAPI + "/fapi/v1/fundingRate", symbol=sym, startTime=t0, endTime=t0 + 8 * 86_400_000,
                               limit=100)
                if not m1:
                    continue
                events.append({"title": a["title"], "symbol": sym, "t0": t0,
                               "m1": [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])]
                                      for b in m1],
                               "h1": [[int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[7])]
                                      for b in (h1 or [])],
                               "funding": [(int(x["fundingTime"]), float(x["fundingRate"])) for x in (fr or [])]})
                await asyncio.sleep(0.3)
        (OUT / "deslistados.json").write_text(json.dumps(events), encoding="utf-8")
        print("monedas con perpetuo:", len(events), flush=True)


asyncio.run(main())
