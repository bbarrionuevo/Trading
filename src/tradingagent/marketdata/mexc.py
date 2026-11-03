"""MEXC futuros (perpetuos USDT-M): datos públicos y lectura privada. NO hay código de órdenes.

Público (sin claves):
- especificación del contrato: tamaño del contrato, tick, lote mínimo, comisiones, margen de mantenimiento
  y apalancamiento máximo;
- libro (las cantidades vienen en contratos y se pasan a unidades del activo);
- velas de 1 minuto, ticker (último precio, volumen de 24 h en USDT) y funding (tasa y próximo cobro).
Privado, solo lectura (REGLAS A-3 y G-1): comisiones de la cuenta y saldos, firmados con HMAC-SHA256. El
cliente solo hace GET y solo a `READ_ONLY_PATHS`: no puede crear órdenes, cambiar apalancamiento ni retirar.
Las claves nunca se imprimen (los errores pasan por `redact`).
"""
import asyncio
import hashlib
import hmac
import time
from decimal import Decimal as D
from urllib.parse import quote, urlencode

import aiohttp

from ..core.redact import redact
from .book import OrderBook
from .providers import MarketDataError

BASE = "https://contract.mexc.com"
MIN = 60_000
READ_ONLY_PATHS = frozenset({"/api/v1/private/account/assets", "/api/v1/private/account/tiered_fee_rate"})


def contract(asset: str) -> str:
    return f"{asset}_USDT"


async def _get(session, url: str, params: dict | None, what: str, timeout_s: float = 10.0, headers=None):
    try:
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
            if r.status != 200:
                raise MarketDataError(f"mexc {what}: HTTP {r.status}")
            body = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise MarketDataError(f"mexc {what}: {type(e).__name__}: {redact(e)[:120]}") from e
    if not isinstance(body, dict) or not body.get("success", False) or body.get("code", 0) != 0:
        code = body.get("code") if isinstance(body, dict) else "?"
        raise MarketDataError(f"mexc {what}: código {code}")
    return body.get("data")


def parse_detail(d: dict) -> dict:
    """Especificación de un contrato, con los números como texto (Decimal exacto)."""
    size = D(str(d["contractSize"]))
    vol_unit = D(str(d.get("volUnit", 1)))
    return {"symbol": d["symbol"], "base": d["baseCoin"], "quote": d.get("quoteCoin", "USDT"),
            "contract_size": str(size), "tick": str(D(str(d["priceUnit"]))), "step": str(size * vol_unit),
            "min_qty": str(size * D(str(d.get("minVol", 1)))), "maker": str(d.get("makerFeeRate", 0)),
            "taker": str(d.get("takerFeeRate", 0)), "mmr": str(d.get("maintenanceMarginRate", 0.005)),
            "max_leverage": int(d.get("maxLeverage", 1)), "trading": d.get("state", 0) == 0}


async def detail(session, symbol: str, base: str = BASE) -> dict:
    return parse_detail(await _get(session, f"{base}/api/v1/contract/detail", {"symbol": symbol}, f"detail {symbol}"))


async def klines(session, symbol: str, start_ms: int, end_ms: int, *, url: str = f"{BASE}/api/v1/contract/kline/{{symbol}}",
                 timeout_s: float = 10.0) -> dict:
    """Velas de 1 minuto: {apertura ms: (open, high, low, close, volumen USDT, None)} (MEXC no da el comprador)."""
    from ..research.labeler import LabelError  # el CandleStore espera este error
    out = {}
    t = start_ms
    while t <= end_ms:
        chunk_end = min(end_ms, t + 999 * MIN)
        try:
            data = await _get(session, url.format(symbol=symbol), {"interval": "Min1", "start": str(t // 1000),
                                                                   "end": str(chunk_end // 1000)}, f"kline {symbol}",
                              timeout_s)
            for i, ts in enumerate(data["time"]):
                out[int(ts) * 1000] = (float(data["open"][i]), float(data["high"][i]), float(data["low"][i]),
                                       float(data["close"][i]), float(data["amount"][i]), None)
        except MarketDataError as e:
            raise LabelError(str(e)) from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LabelError(f"mexc kline {symbol}: formato inesperado") from e
        t = chunk_end + MIN
    return out


def parse_depth(symbol: str, data: dict, contract_size: D, received_at: float) -> OrderBook:
    """Libro de MEXC futuros: [precio, contratos, órdenes] → cantidades en unidades del activo."""
    try:
        bids = [(D(str(x[0])), D(str(x[1])) * contract_size) for x in data["bids"]]
        asks = [(D(str(x[0])), D(str(x[1])) * contract_size) for x in data["asks"]]
    except (KeyError, IndexError, TypeError) as e:
        raise MarketDataError(f"mexc {symbol}: libro con formato inesperado") from e
    ts = data.get("timestamp")
    book = OrderBook.from_levels("mexc-futuros", symbol, bids, asks, ts / 1000 if ts else received_at, received_at)
    if book.problems():
        raise MarketDataError(f"mexc {symbol}: " + "; ".join(book.problems()))
    return book


class ContractBooks:
    """Proveedor de libros de futuros con caché corta y tope de pedidos por segundo."""

    def __init__(self, session, clock, sizes: dict[str, D], *, base: str = BASE, limit: int = 50, cache_ms: int = 250,
                 max_rps: float = 5.0):
        self.session, self.clock, self.sizes, self.base, self.limit = session, clock, sizes, base, limit
        self.cache_s, self.min_gap = cache_ms / 1000, 1.0 / max_rps
        self._cache: dict[str, OrderBook] = {}
        self._last = 0.0
        self._lock = asyncio.Lock()
        self.requests = self.errors = 0

    async def book(self, symbol: str) -> OrderBook:
        cached = self._cache.get(symbol)
        if cached and self.clock.time() - cached.received_at <= self.cache_s:
            return cached
        if symbol not in self.sizes:
            raise MarketDataError(f"mexc {symbol}: sin especificación del contrato")
        async with self._lock:
            wait = self._last + self.min_gap - self.clock.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = self.clock.monotonic()
        self.requests += 1
        try:
            data = await _get(self.session, f"{self.base}/api/v1/contract/depth/{symbol}", {"limit": str(self.limit)},
                              f"depth {symbol}", 3.0)
        except MarketDataError:
            self.errors += 1
            raise
        book = parse_depth(symbol, data, self.sizes[symbol], self.clock.time())
        self._cache[symbol] = book
        return book


async def ticker(session, symbol: str, base: str = BASE) -> dict:
    d = await _get(session, f"{base}/api/v1/contract/ticker", {"symbol": symbol}, f"ticker {symbol}")
    return {"last": float(d["lastPrice"]), "adv": float(d.get("amount24") or 0), "fair": float(d.get("fairPrice") or 0),
            "funding_rate": float(d.get("fundingRate") or 0)}


async def funding(session, symbol: str, base: str = BASE) -> dict:
    """Tasa de funding vigente, próximo cobro (ms) y ciclo (horas)."""
    d = await _get(session, f"{base}/api/v1/contract/funding_rate/{symbol}", None, f"funding {symbol}")
    return {"rate": float(d["fundingRate"]), "next_ms": int(d["nextSettleTime"]), "cycle_h": int(d.get("collectCycle", 8))}


# -- lectura privada (firmada) ------------------------------------------------------------------------
def signature(access_key: str, secret: str, req_time_ms: str, params: dict) -> str:
    """HMAC-SHA256(secreto, clave + hora + parámetros ordenados `k=v&...` con los valores codificados)."""
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), f"{access_key}{req_time_ms}{query}".encode(), hashlib.sha256).hexdigest()


class ReadOnlyClient:
    """Lectura de la cuenta con claves de SOLO LECTURA. Solo GET, solo rutas de `READ_ONLY_PATHS`."""

    def __init__(self, session, access_key: str, secret: str, *, base: str = BASE, clock=None):
        if not access_key or not secret:
            raise ValueError("faltan las claves de lectura de MEXC")
        self.session, self._key, self._secret, self.base, self.clock = session, access_key, secret, base, clock

    def _now_ms(self) -> str:
        return str(int((self.clock.time() if self.clock else time.time()) * 1000))

    async def get(self, path: str, params: dict | None = None):
        if path not in READ_ONLY_PATHS:
            raise PermissionError(f"ruta no permitida para el cliente de solo lectura: {path}")
        params = dict(params or {})
        t = self._now_ms()
        headers = {"ApiKey": self._key, "Request-Time": t, "Signature": signature(self._key, self._secret, t, params),
                   "Content-Type": "application/json"}
        url = f"{self.base}{path}" + (f"?{urlencode(sorted(params.items()))}" if params else "")
        try:
            return await _get(self.session, url, None, path.rsplit("/", 1)[-1], headers=headers)
        except MarketDataError as e:
            raise MarketDataError(redact(e, secrets=(self._key, self._secret))) from None

    async def fee_rates(self, symbol: str) -> dict:
        """Comisiones de la cuenta para un contrato: {"maker": tasa, "taker": tasa}."""
        d = await self.get("/api/v1/private/account/tiered_fee_rate", {"symbol": symbol})
        return {"maker": float(d.get("makerFee", d.get("makerFeeRate", 0))),
                "taker": float(d.get("takerFee", d.get("takerFeeRate", 0)))}

    async def account_ok(self) -> bool:
        """¿Las claves leen la cuenta? (no devuelve saldos: el log del workflow es público)."""
        data = await self.get("/api/v1/private/account/assets")
        return isinstance(data, list)
