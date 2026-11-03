"""Prueba de red desde la PC: qué fuentes candidatas se pueden leer desde ahí (la nube de desarrollo ve otra red:
Upbit y Bybit bloquean por país, Binance bloquea su API).

Una vez por día el agente pide cada URL pública de `CANDIDATES` y deja en `logs/red.json` el código HTTP, la
demora, el tamaño y el comienzo de la respuesta. El supervisor lo publica en la rama `pc`, y así Claude sabe qué
fuentes nuevas sirven antes de agregarlas. Solo URLs públicas, sin claves ni cabeceras propias.
"""
import asyncio
import time

import aiohttp

from ..core.redact import redact

CANDIDATES = {
    "upbit.avisos": "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade",
    "upbit.mercados": "https://api.upbit.com/v1/market/all?isDetails=false",
    "bithumb.avisos": "https://feed.bithumb.com/notice",
    "bybit.avisos": "https://api.bybit.com/v5/announcements/index?locale=en-US&type=new_crypto&limit=20",
    "binance.api": "https://api.binance.com/api/v3/ping",
    "binance.listados": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=48"
                        "&pageNo=1&pageSize=5",
    "coinbase.productos": "https://api.exchange.coinbase.com/products",
    "kraken.activos": "https://api.kraken.com/0/public/Assets",
    "bls.cpi": "https://www.bls.gov/feed/cpi.rss",
}
# Las mismas, verificando el certificado con el almacén de Windows (truststore) en vez del de Python: en la PC
# los avisos de Upbit fallan con "SSLCertVerificationError" (la cadena del servidor está incompleta y Windows la
# completa solo; Python no).
OS_TRUST = {"upbit.avisos.windows": CANDIDATES["upbit.avisos"]}
SAMPLE_CHARS = 300
EVERY_S = 86_400


def os_trust_ssl():
    """Contexto TLS que verifica con el almacén del sistema (Windows/macOS/Linux), o None si no está."""
    try:
        import ssl

        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:                                                # noqa: BLE001 (sin truststore, sin esta prueba)
        return None


async def probe(session, candidates: dict | None = None, timeout_s: float = 15.0,
                os_trust: dict | None = None) -> dict:
    ctx = os_trust_ssl()

    async def one(name: str, url: str, ssl_ctx=None) -> tuple[str, dict]:
        t0 = time.monotonic()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s), ssl=ssl_ctx if ssl_ctx else True,
                                   headers={"User-Agent": "Mozilla/5.0 (compatible; TradingAgent/0.1)"}) as r:
                body = await r.content.read(200_000)
                return name, {"codigo": r.status, "ms": round((time.monotonic() - t0) * 1000),
                              "bytes": len(body), "muestra": redact(body[:SAMPLE_CHARS].decode("utf-8", "replace"))}
        except Exception as e:                                       # noqa: BLE001 (se informa, no se cae)
            return name, {"codigo": None, "ms": round((time.monotonic() - t0) * 1000),
                          "error": f"{type(e).__name__}: {redact(str(e))[:120]}"}

    jobs = [one(n, u) for n, u in (candidates or CANDIDATES).items()]
    if ctx is not None:
        jobs += [one(n, u, ctx) for n, u in (OS_TRUST if os_trust is None else os_trust).items()]
    pairs = await asyncio.gather(*jobs)
    return {"ts": round(time.time()), "fuentes": dict(pairs)}
