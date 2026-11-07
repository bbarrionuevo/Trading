"""Fase 1 del plan de catálogos de Binance (pedido del dueño, 7-nov): una vuelta de pedidos a la vez (`asyncio.gather`)
a los catálogos del CMS de Binance, con el tiempo de ida y vuelta, el estado HTTP, las cabeceras de la entrega
(`x-amz-cf-pop`, `age`, `x-cache`: si la respuesta es una copia de CloudFront y de cuándo), los 2 avisos más recientes
de cada catálogo (`id`, `code`, `title`, `releaseDate`) y la forma del JSON, para diseñar después la extracción de
monedas. También pide la lista SIN catálogo: si trae todos los catálogos en una sola respuesta, sirve para descubrir
categorías nuevas y para leer varias con un solo pedido.

Ojo con el límite de www.binance.com (REGLAS L-7): es por IP y lo comparte el relé de Tokio, que lee el catálogo 48
cada 3 s (y ya recibe algún HTTP 429 por día). Por eso: UNA sola vuelta por corrida (no ponerlo en un bucle) y, si
algún pedido devuelve 429 o 418, lo dice y no reintenta. Solo lee; no usa claves.

Uso (en Tokio, con el .venv del repo):  ~/trading/.venv/bin/python deploy/tokio/test_binance_catalogs.py
  --sin-descubrir   no pide la lista sin catálogo (un pedido menos)
  --compuesta       suma el catálogo 48 por la dirección que usa el relé (bapi/composite), para comparar la copia
"""
import argparse
import asyncio
import json
import sys
import time

import aiohttp

APEX = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo=1&pageSize=10"
COMPOSITE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=10"
CATALOGS = {"LISTINGS": 48, "DELISTINGS": 161, "ACTIVITIES": 93, "MAINTENANCE": 157, "LATEST_NEWS": 49}
HEADERS = ("x-amz-cf-pop", "x-cache", "age", "cache-control", "via", "date", "content-encoding")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TRADING-prueba/1.0", "Accept": "application/json",
      "Accept-Encoding": "gzip, deflate"}


def utc(ms) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(ms) / 1000)) + f".{int(ms) % 1000:03d} UTC"
    except (TypeError, ValueError):
        return str(ms)


def shape(x, depth: int = 0, max_depth: int = 5) -> list[str]:
    pad = "      " + "  " * depth
    if depth > max_depth:
        return [pad + "…"]
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            if isinstance(v, list):
                out.append(f"{pad}{k}: lista de {len(v)}")
                if v and isinstance(v[0], dict | list):
                    out += shape(v[0], depth + 1, max_depth)
            elif isinstance(v, dict):
                out.append(f"{pad}{k}: objeto")
                out += shape(v, depth + 1, max_depth)
            else:
                s = json.dumps(v, ensure_ascii=False)
                out.append(f"{pad}{k}: {type(v).__name__} = {s[:70]}{'…' if len(s) > 70 else ''}")
    return out


async def fetch(session, name: str, url: str) -> dict:
    t0 = time.perf_counter()
    try:
        async with session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=10)) as r:
            body = await r.read()
            return {"name": name, "url": url, "status": r.status, "ms": (time.perf_counter() - t0) * 1000,
                    "headers": {h: r.headers.get(h) for h in HEADERS if r.headers.get(h)}, "body": body}
    except (aiohttp.ClientError, TimeoutError) as e:
        return {"name": name, "url": url, "status": None, "ms": (time.perf_counter() - t0) * 1000,
                "error": f"{type(e).__name__}: {e}"}


def report(res: dict, show: int, full_shape: bool) -> None:
    print(f"\n== {res['name']}\n   {res['url']}")
    if res.get("error"):
        print(f"   FALLÓ en {res['ms']:.0f} ms: {res['error']}")
        return
    print(f"   HTTP {res['status']} · {res['ms']:.0f} ms · {len(res['body'])} bytes")
    for k, v in res["headers"].items():
        print(f"   {k}: {v}")
    if res["status"] != 200:
        print(f"   cuerpo: {res['body'][:200].decode('utf-8', 'replace')}")
        return
    try:
        data = json.loads(res["body"])
    except ValueError:
        print("   no es JSON")
        return
    cats = ((data.get("data") or {}).get("catalogs") or []) if isinstance(data, dict) else []
    if full_shape:
        print("   estructura:")
        for line in shape(data):
            print(line)
    print(f"   catálogos en la respuesta: {len(cats)}")
    for c in cats[: (len(cats) if not show else 40)]:
        arts = c.get("articles") or []
        print(f"   · catálogo {c.get('catalogId')} «{c.get('catalogName')}» (padre {c.get('parentCatalogId')}) · "
              f"{c.get('total', len(arts))} avisos")
        for a in arts[:show]:
            print("       " + json.dumps({"id": a.get("id"), "code": a.get("code"), "title": a.get("title"),
                                         "releaseDate": utc(a.get("releaseDate"))}, ensure_ascii=False)[:300])


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-descubrir", action="store_true")
    ap.add_argument("--compuesta", action="store_true")
    args = ap.parse_args()
    jobs = {f"{name} (catálogo {cid})": f"{APEX}&catalogId={cid}" for name, cid in CATALOGS.items()}
    if args.compuesta:
        jobs["LISTINGS por la dirección del relé (composite)"] = f"{COMPOSITE}&catalogId=48"
    if not args.sin_descubrir:
        jobs["TODOS (sin catálogo: descubrir)"] = APEX.replace("pageSize=10", "pageSize=2")
    print(f"una vuelta de {len(jobs)} pedidos a la vez · {time.strftime('%H:%M:%S', time.gmtime())} UTC")
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(fetch(session, n, u) for n, u in jobs.items()))
    for i, res in enumerate(results):
        report(res, show=2, full_shape=(i == 0))
    print("\nresumen (ms · estado · copia de CloudFront):")
    for res in results:
        h = res.get("headers") or {}
        print(f"   {res['ms']:6.0f} ms · {res.get('status')} · {h.get('x-cache', '—')} · age {h.get('age', '—')} · "
              f"{h.get('x-amz-cf-pop', '—')} · {res['name']}")
    if any(r.get("status") in (418, 429) for r in results):
        print("\nBinance frenó (429/418): no repetir ahora; el límite es por IP y lo comparte el relé (L-7).")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
