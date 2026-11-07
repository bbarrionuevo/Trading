"""Prueba rápida de la API pública de avisos de Upbit (pedido del dueño, 7-nov: "un mini-script de prueba rápido [...]
que haga un simple GET a esa URL pública e imprima la estructura del JSON en consola; quiero que revises cómo vienen
mapeados los campos de texto para poder integrarlo al bot principal").

Para cada dirección: el estado HTTP, cuánto tardó, las cabeceras que dicen si la respuesta viene de una copia (CDN) y de
cuándo, la estructura del JSON (claves, tipos y largo de las listas) y los campos de texto de los primeros avisos. Por
defecto prueba la que encontró el dueño (`emergency_popup/notice`) y, para comparar, la lista de avisos de trading que
el agente lee desde la PC (`announcements`, que desde AWS daba 403).

Uso (en Tokio o en la PC, con el .venv del repo; solo lee y no usa claves):
  python deploy/tokio/prueba_api_upbit.py                 una vuelta a las dos direcciones
  python deploy/tokio/prueba_api_upbit.py --veces 5 --cada 10   repite para ver si la copia del CDN se renueva
  python deploy/tokio/prueba_api_upbit.py --url URL       otra dirección
Upbit limita a ~10 pedidos por minuto por IP (4-nov): no bajar `--cada` de 6 s.
"""
import argparse
import asyncio
import json
import sys
import time

import aiohttp

URLS = {"emergencia (la del dueño)": "https://api-manager.upbit.com/api/v1/emergency_popup/notice?os=web",
        "avisos de trading (los que opera el agente)":
            "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade"}
HEADERS = ("server", "date", "cache-control", "age", "cf-cache-status", "x-cache", "cf-ray", "last-modified", "etag")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TRADING-prueba/1.0", "Accept": "application/json"}
TEXT_KEYS = ("id", "title", "category", "listed_at", "first_listed_at", "created_at", "updated_at", "url", "link",
             "content", "message", "type", "is_pinned", "need_new_badge", "need_update_badge")


def shape(x, indent: int = 2, depth: int = 0, max_depth: int = 6) -> list[str]:
    """La estructura del JSON: claves y tipos; de cada lista, su largo y la forma de su primer elemento."""
    pad = " " * (indent * depth)
    if depth > max_depth:
        return [pad + "…"]
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            kind = type(v).__name__
            if isinstance(v, list):
                out.append(f"{pad}{k}: lista de {len(v)}")
                if v:
                    out += shape(v[0], indent, depth + 1, max_depth)
            elif isinstance(v, dict):
                out.append(f"{pad}{k}: objeto")
                out += shape(v, indent, depth + 1, max_depth)
            else:
                sample = json.dumps(v, ensure_ascii=False)
                out.append(f"{pad}{k}: {kind} = {sample[:80]}{'…' if len(sample) > 80 else ''}")
    elif isinstance(x, list):
        out.append(f"{pad}(lista de {len(x)})")
        if x:
            out += shape(x[0], indent, depth + 1, max_depth)
    else:
        out.append(f"{pad}{type(x).__name__} = {json.dumps(x, ensure_ascii=False)[:80]}")
    return out


def items_of(data) -> list[dict]:
    """Los avisos, estén donde estén: `data.notices`, `data.list`, `data` (lista) o la raíz."""
    for path in (("data", "notices"), ("data", "list"), ("data",), ()):
        node = data
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, list) and node and isinstance(node[0], dict):
            return node
        if isinstance(node, dict) and path == ("data",) and any(k in node for k in ("title", "id")):
            return [node]
    return []


async def probe(session, name: str, url: str, show: int) -> None:
    t0 = time.perf_counter()
    try:
        async with session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=10)) as r:
            body = await r.read()
            ms = (time.perf_counter() - t0) * 1000
            print(f"\n== {name}\n   {url}\n   HTTP {r.status} · {ms:.0f} ms · {len(body)} bytes")
            for h in HEADERS:
                if r.headers.get(h):
                    print(f"   {h}: {r.headers[h]}")
            if r.status != 200:
                print(f"   cuerpo: {body[:200].decode('utf-8', 'replace')}")
                return
    except (aiohttp.ClientError, TimeoutError) as e:
        print(f"\n== {name}\n   {url}\n   FALLÓ en {(time.perf_counter() - t0) * 1000:.0f} ms: {type(e).__name__}: {e}")
        return
    try:
        data = json.loads(body)
    except ValueError:
        print(f"   no es JSON: {body[:200].decode('utf-8', 'replace')}")
        return
    print("   estructura:")
    for line in shape(data, depth=2):
        print(line)
    items = items_of(data)
    print(f"   avisos encontrados: {len(items)}")
    for it in items[:show]:
        fields = {k: it[k] for k in TEXT_KEYS if k in it}
        print("   - " + json.dumps(fields, ensure_ascii=False)[:400])


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="probar solo esta dirección")
    ap.add_argument("--veces", type=int, default=1)
    ap.add_argument("--cada", type=float, default=10.0, help="segundos entre vueltas (nunca menos de 6)")
    ap.add_argument("--mostrar", type=int, default=3, help="cuántos avisos mostrar por dirección")
    args = ap.parse_args()
    urls = {"pedida": args.url} if args.url else URLS
    async with aiohttp.ClientSession() as session:
        for i in range(max(1, args.veces)):
            if i:
                await asyncio.sleep(max(6.0, args.cada))
            print(f"\n######## vuelta {i + 1} · {time.strftime('%H:%M:%S', time.gmtime())} UTC")
            for name, url in urls.items():
                await probe(session, name, url, args.mostrar)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
