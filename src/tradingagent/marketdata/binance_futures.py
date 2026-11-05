"""Binance Futures USDⓈ-M: datos PÚBLICOS para la práctica y el simulacro (sin claves y sin órdenes; las órdenes
reales están en execution/live/binance_futures.py, ruta protegida).

- Catálogo (`/fapi/v1/exchangeInfo`): filtros de cada contrato (tick, lote, cantidad y nominal mínimos), tipo y
  estado. Hay dos familias: los perpetuos de cripto (`PERPETUAL`) y los **TradFi** (`TRADIFI_PERPETUAL`: acciones
  de EE. UU., Corea y Hong Kong, ETF como SPY y QQQ, oro, plata, petróleo, gas y cobre). Todos operan las 24 h,
  también el fin de semana (velas del domingo 1-nov-2026, leídas en la PC). El 4-nov había 703 de cripto y 235
  TradFi; con más de 1 M USDT por día, 449 y 170.
- Volumen de 24 h (`/fapi/v1/ticker/24hr`), libro (`/fapi/v1/depth`), velas de 1 minuto (`/fapi/v1/klines`, el
  mismo formato que el spot), precio de marca y funding (`/fapi/v1/premiumIndex`; el intervalo sale de
  `/fapi/v1/fundingInfo`: 8 h en general, 4 h el oro).
Anda desde la PC (Argentina). Desde EE. UU. (la nube de desarrollo y GitHub) Binance responde 451: ahí se prueba
contra un Binance falso.
"""
import asyncio
import logging
from decimal import Decimal as D

import aiohttp

from ..core.redact import redact
from .peso import MONITOR
from .providers import MarketDataError, RestBookProvider

log = logging.getLogger("tradingagent.binance")
BASE = "https://fapi.binance.com"
PERPS = ("PERPETUAL", "TRADIFI_PERPETUAL")
# Fuera del universo: los índices de cripto (BTCDOM: ninguna noticia los nombra) y las empresas que todavía no
# cotizan (OpenAI, Anthropic: casi toda noticia las nombra y casi ninguna mueve ese contrato).
SKIP_UNDERLYING = ("INDEX", "PREMARKET")


async def get(session, path: str, params: dict | None = None, what: str = "", *, base: str = BASE,
              timeout_s: float = 10.0):
    try:
        async with session.get(f"{base}{path}", params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
            MONITOR.observe(r.headers)                           # peso usado de la IP (marketdata/peso.py)
            if r.status != 200:
                raise MarketDataError(f"binance {what or path}: HTTP {r.status}")
            return await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise MarketDataError(f"binance {what or path}: {type(e).__name__}: {redact(e)[:120]}") from e


def parse_symbol(s: dict) -> dict | None:
    """Un contrato del catálogo, con los números como texto (Decimal exacto). None si no es un perpetuo en USDT."""
    if s.get("contractType") not in PERPS or s.get("quoteAsset") != "USDT" or s.get("marginAsset", "USDT") != "USDT":
        return None
    f = {x.get("filterType"): x for x in s.get("filters", [])}
    try:
        lot = f["LOT_SIZE"]
        required = D(str(s.get("requiredMarginPercent") or "5"))
        return {"symbol": s["symbol"], "base": s["baseAsset"], "quote": "USDT",
                "tick": str(D(str(f["PRICE_FILTER"]["tickSize"])).normalize()),
                "step": str(D(str(lot["stepSize"])).normalize()), "min_qty": str(D(str(lot["minQty"])).normalize()),
                "min_notional": str((f.get("MIN_NOTIONAL") or {}).get("notional", "5")),
                "mmr": str(D(str(s.get("maintMarginPercent") or "2.5")) / 100),
                "max_leverage": max(1, int(D(100) / required)) if required > 0 else 1,
                "tipo": s["contractType"], "subyacente": s.get("underlyingType") or "COIN",
                "trading": s.get("status") == "TRADING",
                "alta": float(s.get("onboardDate") or 0) / 1000}    # cuándo empezó a operar (s)
    except (KeyError, TypeError, ArithmeticError):
        return None


async def catalog(session, base: str = BASE) -> dict[str, dict]:
    """Todos los perpetuos en USDT, de cripto y TradFi (un solo pedido, peso 1). No es urgente: espera si el peso de
    la IP pasó el 80 %."""
    await MONITOR.wait_if_busy()
    data = await get(session, "/fapi/v1/exchangeInfo", None, "catálogo", base=base, timeout_s=20)
    out = {}
    for s in (data or {}).get("symbols", []):
        p = parse_symbol(s)
        if p:
            out[p["symbol"]] = p
    return out


async def volumes(session, base: str = BASE) -> dict[str, float]:
    """Volumen de 24 h en USDT de cada contrato (peso 40). No es urgente: espera si el peso de la IP pasó el 80 %."""
    await MONITOR.wait_if_busy()
    data = await get(session, "/fapi/v1/ticker/24hr", None, "volúmenes", base=base, timeout_s=20)
    return {t["symbol"]: float(t.get("quoteVolume") or 0) for t in data or [] if isinstance(t, dict) and "symbol" in t}


async def ticker(session, symbol: str, base: str = BASE) -> dict:
    """Último precio, volumen de 24 h, precio de marca y funding vigente (dos pedidos a la vez)."""
    day, prem = await asyncio.gather(get(session, "/fapi/v1/ticker/24hr", {"symbol": symbol}, f"ticker {symbol}",
                                         base=base),
                                     get(session, "/fapi/v1/premiumIndex", {"symbol": symbol}, f"marca {symbol}",
                                         base=base))
    try:
        return {"last": float(day["lastPrice"]), "adv": float(day.get("quoteVolume") or 0),
                "fair": float(prem.get("markPrice") or 0), "funding_rate": float(prem.get("lastFundingRate") or 0)}
    except (KeyError, TypeError, ValueError) as e:
        raise MarketDataError(f"binance ticker {symbol}: formato inesperado") from e


async def premium(session, symbol: str, base: str = BASE) -> dict:
    d = await get(session, "/fapi/v1/premiumIndex", {"symbol": symbol}, f"funding {symbol}", base=base)
    try:
        return {"mark": float(d["markPrice"]), "rate": float(d.get("lastFundingRate") or 0),
                "next_ms": int(d.get("nextFundingTime") or 0)}
    except (KeyError, TypeError, ValueError) as e:
        raise MarketDataError(f"binance funding {symbol}: formato inesperado") from e


async def funding_hours(session, base: str = BASE) -> dict[str, int]:
    """Intervalo de cobro de los contratos que no cobran cada 8 h (el oro, cada 4). No es urgente."""
    await MONITOR.wait_if_busy()
    data = await get(session, "/fapi/v1/fundingInfo", None, "intervalos de funding", base=base)
    return {x["symbol"]: int(x.get("fundingIntervalHours") or 8) for x in data or []
            if isinstance(x, dict) and "symbol" in x}


def books(session, clock, base: str = BASE) -> RestBookProvider:
    """Libro de 50 niveles (peso 2 de los 2.400 por minuto), caché de 250 ms y hasta 5 pedidos por segundo."""
    return RestBookProvider("binance-futuros", session, clock, base_url=f"{base}/fapi/v1/depth", depth=50, max_rps=5)
