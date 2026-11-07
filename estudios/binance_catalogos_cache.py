"""Fase 2 del plan de catálogos (7-nov): ¿se puede leer la lista de anuncios de Binance SIN catálogo (los 7 juntos:
listados, deslistados, noticias, actividades, mantenimiento, API y airdrops) tan fresca como hoy el relé lee el 48?

El relé saltea la copia del CDN (CloudFront guarda cada forma de la dirección ~120 s) rotando 72 formas del pedido del
catálogo 48: cada forma se vuelve a pedir ya vencida. Sin catálogo hay un parámetro menos (6 órdenes × 5 tamaños = 30
formas por dirección), así que se mide, para la dirección `composite` (la del relé) y la `apex`:

1. qué tamaños de página y órdenes de parámetros acepta (200 contra 400) y qué trae (catálogos, avisos, bytes);
2. si la respuesta es copia (`x-cache`, `age`) y cuánto dura la copia: la misma forma pedida una y otra vez.

Desde la PC (no desde Tokio: el límite de www.binance.com es por IP y allá lo usa el relé), un pedido cada 30 s y se
corta con el primer 429 o 418 (REGLAS L-7). Solo lee, sin claves.

Uso: python estudios/binance_catalogos_cache.py <carpeta>   (deja <carpeta>/catalogos-cache.json)
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
PATHS = {"composite": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
         "apex": "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"}
UA = {"User-Agent": "Mozilla/5.0 (estudio de TRADING, solo lectura)", "Accept": "application/json"}
PAUSE_S = 30.0
HEADERS = ("x-cache", "age", "x-amz-cf-pop", "cache-control")


def url(path: str, size: int, order: tuple[str, ...] = ("type", "pageNo", "pageSize")) -> str:
    values = {"type": "type=1", "pageNo": "pageNo=1", "pageSize": f"pageSize={size}"}
    return f"{PATHS[path]}?{'&'.join(values[k] for k in order)}"


def get(u: str) -> dict:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
            body, status, hdr = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        body, status, hdr = e.read(), e.code, e.headers
    out = {"url": u, "status": status, "ms": round((time.perf_counter() - t0) * 1000),
           "at": round(time.time(), 1), "bytes": len(body), **{h: hdr.get(h) for h in HEADERS}}
    if status == 200:
        try:
            cats = (json.loads(body).get("data") or {}).get("catalogs") or []
        except ValueError:
            cats = []
        out["catalogos"] = {str(c.get("catalogId")): len(c.get("articles") or []) for c in cats}
        newest = [a.get("releaseDate") or 0 for c in cats for a in (c.get("articles") or [])]
        out["mas_nuevo_s"] = round(time.time() - max(newest) / 1000) if newest else None
    return out


def main() -> int:
    plan = [("composite", 10, None), ("apex", 10, None)]
    plan += [(p, s, None) for p in PATHS for s in (5, 15, 20, 50)]
    plan += [(p, 10, o) for p in PATHS for o in (("pageSize", "type", "pageNo"), ("pageNo", "pageSize", "type"))]
    plan += [(p, 10, None) for _ in range(5) for p in PATHS]          # la misma forma otra vez: ¿cuánto dura la copia?
    rows = []
    for i, (path, size, order) in enumerate(plan):
        if i:
            time.sleep(PAUSE_S)
        r = get(url(path, size, order) if order else url(path, size))
        r["direccion"], r["tamano"] = path, size
        rows.append(r)
        print(f"{time.strftime('%H:%M:%S')} {path:9} tamaño {size:2} {'orden ' + '/'.join(order) if order else '':30} "
              f"HTTP {r['status']} · {r['ms']} ms · {r['bytes']} B · {r.get('x-cache')} · age {r.get('age')} · "
              f"catálogos {r.get('catalogos')} · lo más nuevo hace {r.get('mas_nuevo_s')} s", flush=True)
        (OUT / "catalogos-cache.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        if r["status"] in (418, 429):
            print("Binance frenó (429/418): corto acá (L-7).")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
