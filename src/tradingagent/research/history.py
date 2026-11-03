"""Estudio histórico de listados (pedido del dueño, 3-nov: "buscar en historial para que pueda entender más rápido
cómo repercuten las noticias en el mercado").

Para cada anuncio oficial de Binance de los últimos `days` días ("Binance Will List X (FOO)" y "Binance Futures
Will Launch USDⓈ-Margined FOOUSDT Perpetual"), mide cómo se movió FOO en MEXC futuros desde la apertura de la vela
del anuncio: a los 5, 15, 60 y 240 minutos, y el pico en 4 h. MEXC guarda velas de 5 min de unos 6 meses.

Resultado del 3-nov-2026 (docs/ESTUDIO-LISTADOS.md): los listados spot siguen subiendo hasta la hora (mediana
+12,5 % a 5 min, +28 % a 60 min, n = 7); los lanzamientos de futuros saltan y se devuelven (mediana +5,9 % a 5 min,
≈ 0 % a 60 min, n = 9). Sirve para calibrar (solo con evidencia, C-5) y para la revisión diaria.
"""
import asyncio
import re
import statistics
import time

import aiohttp

from ..marketdata import mexc
from ..marketdata.providers import MarketDataError

BINANCE_PAGE = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=48"
                "&pageNo={page}&pageSize=20")
SPOT = re.compile(r"Binance Will List .+? \(([A-Z0-9]{2,15})\)")
FUTURES = re.compile(r"Binance Futures Will Launch USD.-Margined ([A-Z0-9]{2,15}?)USDT Perpetual")
HORIZONS_MIN = (5, 15, 60, 240)
STEP_S = 300


async def _json(session, url: str):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20),
                           headers={"User-Agent": "Mozilla/5.0 (compatible; TradingAgent/0.1)"}) as r:
        if r.status != 200:
            raise MarketDataError(f"HTTP {r.status}")
        return await r.json(content_type=None)


async def binance_events(session, days: int, now: float, page_url: str = BINANCE_PAGE,
                         max_pages: int = 20) -> list[dict]:
    """Anuncios de listado (spot) y de lanzamiento de futuros de Binance de los últimos `days` días."""
    out = []
    for page in range(1, max_pages + 1):
        data = await _json(session, page_url.format(page=page))
        arts = (((data or {}).get("data") or {}).get("catalogs") or [{}])[0].get("articles") or []
        for a in arts:
            t = float(a.get("releaseDate") or 0) / 1000
            for kind, rx in (("listado_spot", SPOT), ("futuros", FUTURES)):
                m = rx.search(a.get("title") or "")
                if m and t >= now - days * 86400:
                    out.append({"tipo": kind, "ticker": m.group(1), "t": t, "titulo": a["title"]})
        if not arts or float(arts[-1].get("releaseDate") or 0) / 1000 < now - days * 86400:
            break
    return out


def reaction(candles: dict[int, tuple], t: float) -> dict | None:
    """Retornos desde la apertura de la vela del anuncio: a cada horizonte (cierre de la vela) y el pico en 4 h."""
    b = int(t) // STEP_S * STEP_S
    base = candles.get(b)
    if not base or base[0] <= 0:
        return None
    p0 = base[0]
    out = {}
    for h in HORIZONS_MIN:
        c = candles.get(b + h * 60 - STEP_S)
        out[f"r{h}"] = None if c is None else round(c[3] / p0 - 1, 5)
    highs = [c[1] for ts, c in candles.items() if b <= ts < b + 240 * 60]
    out["pico_4h"] = round(max(highs) / p0 - 1, 5) if highs else None
    return out


def summarize(rows: list[dict]) -> dict:
    out = {}
    for kind in sorted({r["tipo"] for r in rows}):
        sub = [r for r in rows if r["tipo"] == kind and r.get("reaccion")]
        stats = {}
        for k in (*(f"r{h}" for h in HORIZONS_MIN), "pico_4h"):
            xs = [r["reaccion"][k] for r in sub if r["reaccion"].get(k) is not None]
            if xs:
                stats[k] = {"mediana": round(statistics.median(xs), 4), "media": round(statistics.fmean(xs), 4),
                            "n": len(xs), "positivos": sum(x > 0 for x in xs)}
        out[kind] = stats
    return out


async def listing_study(session, *, days: int = 180, now: float | None = None, page_url: str = BINANCE_PAGE,
                        base: str = mexc.BASE) -> dict:
    now = time.time() if now is None else now
    events = await binance_events(session, days, now, page_url)
    rows = []
    for e in events:
        sym = f"{e['ticker']}_USDT"
        t = int(e["t"])
        try:
            k = await mexc._get(session, f"{base}/api/v1/contract/kline/{sym}",
                                {"interval": "Min5", "start": t - 1800, "end": t + 4 * 3600}, f"velas {sym}")
            candles = {int(ts): (float(o), float(h), float(lo), float(c)) for ts, o, h, lo, c in
                       zip(k.get("time", []), k.get("open", []), k.get("high", []), k.get("low", []),
                           k.get("close", []), strict=False)}
        except (MarketDataError, AttributeError, TypeError, ValueError):
            candles = {}
        rows.append({**e, "simbolo_mexc": sym, "reaccion": reaction(candles, e["t"])})
        await asyncio.sleep(0.2)
    return {"ts": round(now), "dias": days, "eventos": rows, "resumen": summarize(rows)}
