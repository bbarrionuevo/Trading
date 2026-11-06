"""Bithumb: ¿cuánto tarda en verse un aviso nuevo? (el dueño, 6-nov: "Sí, medila, y si te lo bloquea busca otras
alternativas (ej telegram)").

Sus altas en wones mueven el perpetuo de Binance como Upbit, pero en 2026 la ventana es de ~1 s
(`estudios/otros_listados_segundos.py`). Dos fuentes, una vez por segundo cada una, con la conexión abierta:

- **gw** (`gw.bithumb.com/feed/v2/customer-support/articles/notices/recent`): la que usa la web de trading de Bithumb
  para sus avisos. Sin caché (`no-cache, no-store, max-age=0`), responde a un programa desde Tokio en ~120 ms.
- **api** (`feed-api.bithumb.com/v1/notices`, a donde redirige la API pública `api.bithumb.com/v1/notices`): pasa por
  Cloudflare con `max-age=14400` (4 h) y una clave de caché que ignora los parámetros (el 6-nov la copia tenía 41 min).
  Si Bithumb no la limpia al publicar, un aviso tarda horas.

Por cada aviso nuevo y cada fuente anota cuándo apareció, su hora de publicación (Corea, al segundo), la demora y, en la
api, la edad de la copia (`age`). Cada 10 min, un latido con los contadores (y los estados HTTP: un 403 seguido es que
la fuente nos bloqueó). Para solo a las `--horas`. Los avisos que ya estaban al arrancar no cuentan.

Uso (en Tokio: ~20 MB, dos pedidos por segundo, solo la biblioteca estándar):
  nohup nice -n 19 python3 bithumb_demora.py ~/diag/bithumb-demora.jsonl --horas 72 > /dev/null 2>&1 &
"""
import argparse
import calendar
import http.client
import json
import ssl
import time

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "ko-KR,ko;q=0.9",
      "Referer": "https://www.bithumb.com/", "Origin": "https://www.bithumb.com"}
SOURCES = {"gw": ("gw.bithumb.com", "/feed/v2/customer-support/articles/notices/recent"),
           "api": ("feed-api.bithumb.com", "/v1/notices?count=5")}


def kst_to_utc(s) -> float | None:
    try:
        return calendar.timegm(time.strptime(str(s), "%Y-%m-%d %H:%M:%S")) - 9 * 3600
    except (TypeError, ValueError):
        return None


def notices(name: str, body: bytes) -> dict[str, dict]:
    """{id del aviso: {titulo, publicado}} de cada fuente (el mismo id en las dos: se comparan)."""
    d = json.loads(body)
    out = {}
    if name == "gw":
        for x in (d.get("data") or []) if isinstance(d, dict) else []:
            out[str(x.get("id"))] = {"titulo": x.get("title"), "publicado": x.get("publicationDateTime"),
                                     "categoria": x.get("categoryName1")}
    else:
        for x in d if isinstance(d, list) else []:
            nid = str(x.get("pc_url") or "").rstrip("/").rsplit("/", 1)[-1]
            out[nid] = {"titulo": x.get("title"), "publicado": x.get("published_at"),
                        "categoria": (x.get("categories") or [None])[0]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("salida")
    ap.add_argument("--horas", type=float, default=72.0)
    ap.add_argument("--cada", type=float, default=1.0, help="segundos entre pedidos a cada fuente")
    args = ap.parse_args()
    ctx = ssl.create_default_context()
    end = time.time() + args.horas * 3600
    conns: dict[str, http.client.HTTPSConnection | None] = {n: None for n in SOURCES}
    seen: dict[str, set | None] = {n: None for n in SOURCES}
    counts = {n: {"pedidos": 0, "errores": 0, "nuevos": 0, "estados": {}} for n in SOURCES}
    pause: dict[str, float] = {n: 0.0 for n in SOURCES}
    last_beat, last_age = 0.0, None

    def log(row: dict) -> None:
        with open(args.salida, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    log({"ts": round(time.time(), 3), "tipo": "inicio", "horas": args.horas, "cada_s": args.cada,
         "fuentes": {n: h + p for n, (h, p) in SOURCES.items()}})
    while time.time() < end:
        for name, (host, path) in SOURCES.items():
            t0 = time.time()
            if t0 < pause[name]:
                continue
            c = counts[name]
            try:
                if conns[name] is None:
                    conns[name] = http.client.HTTPSConnection(host, timeout=10, context=ctx)
                conns[name].request("GET", path, headers=UA)
                r = conns[name].getresponse()
                body = r.read()
                ms = round((time.time() - t0) * 1000, 1)
                c["pedidos"] += 1
                c["estados"][str(r.status)] = c["estados"].get(str(r.status), 0) + 1
                if r.status != 200:
                    log({"ts": round(t0, 3), "tipo": "http", "fuente": name, "estado": r.status, "ms": ms})
                    pause[name] = time.time() + (60 if r.status in (403, 429) else 5)
                    continue
                if name == "api":
                    last_age = r.getheader("age")
                got, now = notices(name, body), time.time()
                if seen[name] is None:
                    seen[name] = set(got)                         # lo que ya estaba: no es nuevo
                for nid, x in got.items():
                    if nid in seen[name]:
                        continue
                    seen[name].add(nid)
                    c["nuevos"] += 1
                    pub = kst_to_utc(x["publicado"])
                    log({"ts": round(now, 3), "tipo": "nuevo", "fuente": name, "id": nid,
                         "demora_s": None if pub is None else round(now - pub, 1), "publicado_utc": pub,
                         "ms": ms, "edad_copia": r.getheader("age") if name == "api" else None,
                         "categoria": x["categoria"], "titulo": str(x["titulo"])[:120]})
            except (OSError, http.client.HTTPException, ValueError) as e:
                c["errores"] += 1
                log({"ts": round(t0, 3), "tipo": "error", "fuente": name, "error": f"{type(e).__name__}: {str(e)[:120]}"})
                if conns[name] is not None:
                    conns[name].close()
                conns[name] = None
                pause[name] = time.time() + 5
        if time.time() - last_beat >= 600:
            last_beat = time.time()
            log({"ts": round(last_beat, 3), "tipo": "latido", "contadores": counts, "edad_copia_api": last_age})
        time.sleep(args.cada)
    log({"ts": round(time.time(), 3), "tipo": "fin", "contadores": counts})


if __name__ == "__main__":
    main()
