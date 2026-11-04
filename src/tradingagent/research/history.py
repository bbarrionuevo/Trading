"""Estudio histórico de listados (pedido del dueño, 3-nov: "buscar en historial para que pueda entender más rápido
cómo repercuten las noticias en el mercado").

Para cada anuncio oficial de Binance de los últimos `days` días ("Binance Will List X (FOO)" y "Binance Futures
Will Launch USDⓈ-Margined FOOUSDT Perpetual"), mide cómo se movió FOO en MEXC futuros desde la apertura de la vela
del anuncio: a los 5, 15, 60 y 240 minutos, y el pico en 4 h. MEXC guarda velas de 5 min de unos 6 meses.

Resultado del 3-nov-2026 (docs/ESTUDIO-LISTADOS.md): los listados spot siguen subiendo hasta la hora (mediana
+12,5 % a 5 min, +28 % a 60 min, n = 7); los lanzamientos de futuros saltan y se devuelven (mediana +5,9 % a 5 min,
≈ 0 % a 60 min, n = 9). Sirve para calibrar (solo con evidencia, C-5) y para la revisión diaria.

`exchange="bybit"`: lo mismo con los avisos de Bybit ("Bybit to List Foo (FOO) on Spot" y los perpetuos de
criptos; los "TradFi" son acciones y no cuentan). Su API solo responde desde la PC (desde la nube está bloqueada).

`exchange="upbit"`: los avisos de trading de Upbit, en coreano, desde la PC (certificado con el almacén de
Windows): listado nuevo ("신규 거래지원"), alta en el mercado en wones ("KRW 마켓 디지털 자산 추가"), fin del
soporte ("거래지원 종료"), advertencia ("유의 종목 지정", "유의 촉구") y advertencia levantada ("지정 해제"). La hora
es la de la primera publicación (`first_listed_at`): Upbit edita avisos y mueve `listed_at`.
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
BYBIT_PAGE = "https://api.bybit.com/v5/announcements/index?locale=en-US&type=new_crypto&limit=50&page={page}"
BYBIT_SPOT = re.compile(r"(?i:Bybit\s+(?:to\s+|will\s+)?lists?)\s+.+?\(([A-Z0-9]{2,15})\)|"
                        r"New listing:\s+([A-Z0-9]{2,15})/USDT\b")
BYBIT_FUTURES = re.compile(r"New listing: (?:Listing of )?([A-Z0-9]{2,15}?)USDT (?:on Bybit )?Perpetual")
UPBIT_PAGE = "https://api-manager.upbit.com/api/v1/announcements?os=web&page={page}&per_page=20&category=trade"
UPBIT_TICKER = re.compile(r"\(([A-Z0-9]{1,15})\)")
UPBIT_KINDS = (("alerta_levantada", re.compile(r"지정\s*해제")),         # primero: "지정 해제" también dice "지정"
               ("alerta_extendida", re.compile(r"지정\s*기간\s*연장")),
               ("listado_spot", re.compile(r"신규\s*거래지원")),
               ("listado_krw", re.compile(r"KRW[^)]*마켓\s*디지털\s*자산\s*추가")),
               ("deslistado", re.compile(r"거래지원\s*종료")),
               ("alerta", re.compile(r"유의\s*종목\s*지정(?!\s*기간)|유의\s*촉구")))
HORIZONS_MIN = (5, 15, 60, 240)
STEP_S = 300


async def _json(session, url: str, ssl=None):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), ssl=ssl,
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


async def bybit_events(session, days: int, now: float, page_url: str = BYBIT_PAGE, max_pages: int = 60) -> list[dict]:
    """Listados spot y perpetuos de criptos anunciados por Bybit en los últimos `days` días (el primer aviso de cada
    moneda: Bybit repite avisos, por ejemplo con promociones)."""
    first: dict[tuple, dict] = {}
    since = now - days * 86400
    for page in range(1, max_pages + 1):                 # no vienen en orden estricto (el 3-nov la página 9 era toda
        data = await _json(session, page_url.format(page=page))     # vieja y la 10 no): se leen todas (~37)
        arts = ((data or {}).get("result") or {}).get("list") or []
        if not arts:
            break
        await asyncio.sleep(0.2)
        for a in arts:
            t = float(a.get("publishTime") or a.get("dateTimestamp") or 0) / 1000
            title = a.get("title") or ""
            if t < since or "tradfi" in title.lower():                # TradFi: acciones, no criptos
                continue
            for kind, rx in (("listado_spot", BYBIT_SPOT), ("futuros", BYBIT_FUTURES)):
                m = rx.search(title)
                if m:
                    key = (kind, next(g for g in m.groups() if g).upper())
                    if key not in first or t < first[key]["t"]:
                        first[key] = {"tipo": kind, "ticker": key[1], "t": t, "titulo": title}
                    break
    return sorted(first.values(), key=lambda e: e["t"])


def _iso(s: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except ValueError:
        return 0.0


async def upbit_events(session, days: int, now: float, page_url: str = UPBIT_PAGE, max_pages: int = 60,
                       ssl=None, pause_s: float = 2.0) -> list[dict]:
    """Avisos de trading de Upbit de los últimos `days` días, por tipo (UPBIT_KINDS), con la hora de la primera
    publicación. Upbit solo responde desde la PC y con el almacén de certificados del sistema (`ssl`).
    Despacio (`pause_s` entre páginas) y esperando si Upbit frena (HTTP 429): la fuente en vivo del agente también
    lo lee cada 10 s y no hay que gastarle el cupo."""
    out = []
    since = now - days * 86400
    for page in range(1, max_pages + 1):
        for wait in (0, 20 * pause_s, 60 * pause_s):
            await asyncio.sleep(wait)
            try:
                data = await _json(session, page_url.format(page=page), ssl=ssl)
                break
            except MarketDataError as e:
                if "429" not in str(e) or wait == 60 * pause_s:
                    raise
        notes = ((data or {}).get("data") or {}).get("notices") or []
        if not notes:
            break
        await asyncio.sleep(pause_s)
        stamps = []
        for a in notes:
            t = _iso(a.get("first_listed_at") or a.get("listed_at") or "")
            title = str(a.get("title") or "")
            stamps.append(t)
            m = UPBIT_TICKER.search(title)
            if t < since or not m:
                continue
            kind = next((k for k, rx in UPBIT_KINDS if rx.search(title)), None)
            if kind:
                out.append({"tipo": kind, "ticker": m.group(1), "t": t, "titulo": title})
        if max(stamps) < since:                                       # vienen por fecha: ya pasamos la ventana
            break
    return sorted(out, key=lambda e: e["t"])


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


async def listing_study(session, *, days: int = 180, now: float | None = None, page_url: str | None = None,
                        base: str = mexc.BASE, exchange: str = "binance") -> dict:
    now = time.time() if now is None else now
    if exchange == "bybit":
        events = await bybit_events(session, days, now, page_url or BYBIT_PAGE)
    elif exchange == "upbit":
        from .probe import os_trust_ssl
        events = await upbit_events(session, days, now, page_url or UPBIT_PAGE,
                                    ssl=None if page_url else os_trust_ssl(), pause_s=0 if page_url else 2.0)
    else:
        events = await binance_events(session, days, now, page_url or BINANCE_PAGE)
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
    return {"ts": round(now), "dias": days, "exchange": exchange, "eventos": rows, "resumen": summarize(rows)}
