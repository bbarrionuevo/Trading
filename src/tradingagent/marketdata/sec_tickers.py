"""De CIK (el número de la empresa en la SEC) a su ticker, con la lista pública de la SEC
(`https://www.sec.gov/files/company_tickers.json`, pide el User-Agent con nombre y contacto: SEC_USER_AGENT). Se guarda
en disco y se renueva una vez por día. Una empresa puede tener varios tickers (Alphabet: GOOGL y GOOG)."""
import logging
import time
from pathlib import Path

import aiohttp

from .. import paths

log = logging.getLogger("tradingagent.sec")
URL = "https://www.sec.gov/files/company_tickers.json"
TTL_S = 86_400


def parse(data: dict) -> dict[int, tuple[str, ...]]:
    out: dict[int, list[str]] = {}
    for v in (data or {}).values():
        try:
            out.setdefault(int(v["cik_str"]), []).append(str(v["ticker"]).upper())
        except (KeyError, TypeError, ValueError):
            continue
    return {k: tuple(v) for k, v in out.items()}


async def load(session, cache: Path | None, user_agent: str | None, *, url: str = URL,
               now: float | None = None) -> dict[int, tuple[str, ...]]:
    """El mapa CIK → tickers: de la caché si tiene menos de un día; si no, de la SEC (y se guarda). Sin red o sin
    User-Agent, la caché vieja; sin nada, vacío."""
    now = time.time() if now is None else now
    cached = paths.read_json(cache, None) if cache else None
    if isinstance(cached, dict) and now - float(cached.get("ts", 0)) < TTL_S:
        return {int(k): tuple(v) for k, v in (cached.get("mapa") or {}).items()}
    old = {int(k): tuple(v) for k, v in ((cached or {}).get("mapa") or {}).items()} if isinstance(cached, dict) else {}
    if not user_agent:
        return old
    try:
        async with session.get(url, headers={"User-Agent": user_agent},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                raise ValueError(f"HTTP {r.status}")
            mapa = parse(await r.json(content_type=None))
    except Exception as e:                                           # noqa: BLE001 (sin la lista se sigue igual)
        log.warning("no pude leer la lista de tickers de la SEC: %s", str(e)[:120])
        return old
    if cache and mapa:
        paths.write_json_atomic(cache, {"ts": round(now), "mapa": {str(k): list(v) for k, v in mapa.items()}})
    return mapa
