"""Proveedores de libros de órdenes para el paper: estático (tests), grabado (reproducción) y REST
público en vivo (Binance / MEXC, solo lectura, sin claves).

Un libro viejo no sirve para decidir ni para simular un llenado (REGLAS D-3): el venue de paper
rechaza la orden si el libro tiene más de `max_book_age_s`. El proveedor REST respeta un tope de
pedidos por segundo y reutiliza la última instantánea si es más nueva que `cache_ms`.
"""
import asyncio
import bisect
import json
from pathlib import Path
from typing import Protocol

import aiohttp

from ..core.redact import redact
from .book import OrderBook
from .peso import MONITOR


class MarketDataError(Exception):
    pass


class BookProvider(Protocol):
    async def book(self, symbol: str) -> OrderBook: ...


class StaticBookProvider:
    """Libro fijado a mano; `set` lo reemplaza (tests y escenarios del harness)."""

    def __init__(self, books: dict[str, OrderBook] | None = None):
        self.books = dict(books or {})
        self.calls = 0

    def set(self, book: OrderBook) -> None:
        self.books[book.symbol] = book

    async def book(self, symbol: str) -> OrderBook:
        self.calls += 1
        try:
            return self.books[symbol]
        except KeyError as e:
            raise MarketDataError(f"sin libro para {symbol}") from e


class ReplayBookProvider:
    """Instantáneas grabadas (JSONL: venue, symbol, ts, bids, asks). Devuelve la última con ts ≤ reloj."""

    def __init__(self, books: list[OrderBook], clock):
        self.clock = clock
        self._by_symbol: dict[str, list[OrderBook]] = {}
        for b in sorted(books, key=lambda b: b.ts):
            self._by_symbol.setdefault(b.symbol, []).append(b)
        self._ts = {s: [b.ts for b in bs] for s, bs in self._by_symbol.items()}

    @classmethod
    def from_jsonl(cls, path, clock) -> "ReplayBookProvider":
        books = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                d = json.loads(line)
                books.append(OrderBook.from_levels(d.get("venue", "replay"), d["symbol"], d["bids"], d["asks"],
                                                   d["ts"]))
        return cls(books, clock)

    async def book(self, symbol: str) -> OrderBook:
        ts = self._ts.get(symbol)
        if not ts:
            raise MarketDataError(f"sin grabación para {symbol}")
        i = bisect.bisect_right(ts, self.clock.time()) - 1
        if i < 0:
            raise MarketDataError(f"{symbol}: no hay libro antes de {self.clock.time()}")
        b = self._by_symbol[symbol][i]
        return OrderBook(b.venue, b.symbol, b.bids, b.asks, b.ts, self.clock.time())


VENUES = {
    "binance": "https://api.binance.com/api/v3/depth",
    "mexc": "https://api.mexc.com/api/v3/depth",
}


def parse_depth(venue: str, symbol: str, data: dict, received_at: float) -> OrderBook:
    """Formato de Binance y MEXC: {"lastUpdateId", "bids": [["precio","cant"],...], "asks": [...]}"""
    try:
        bids, asks = data["bids"], data["asks"]
    except (KeyError, TypeError) as e:
        raise MarketDataError(f"{venue} {symbol}: respuesta sin bids/asks") from e
    ts = data.get("T") or data.get("E")                       # algunos endpoints informan la hora en ms
    book = OrderBook.from_levels(venue, symbol, bids, asks, (ts / 1000) if ts else received_at, received_at)
    if book.problems():
        raise MarketDataError(f"{venue} {symbol}: " + "; ".join(book.problems()))
    return book


class RestBookProvider:
    def __init__(self, venue: str, session: aiohttp.ClientSession, clock, *, depth: int = 50, cache_ms: int = 250,
                 max_rps: float = 5.0, timeout_s: float = 3.0, base_url: str | None = None):
        if venue not in VENUES and not base_url:
            raise ValueError(f"venue sin endpoint conocido: {venue}")
        self.venue, self.session, self.clock = venue, session, clock
        self.url = base_url or VENUES[venue]
        self.depth, self.cache_s, self.rate = depth, cache_ms / 1000, float(max_rps)
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._cache: dict[str, OrderBook] = {}
        # Balde de fichas (5-nov): el mismo promedio de pedidos por segundo, pero varios pueden salir juntos. Antes había
        # 200 ms obligatorios entre dos pedidos cualesquiera: el libro de un deslistado esperaba detrás del de una
        # posición abierta (y la tercera moneda de un aviso, 400 ms), cuando el precio cae entre los 2 y 5 s.
        self.burst = max(1.0, float(max_rps))
        self._tokens, self._refill_at = self.burst, None
        self._lock = asyncio.Lock()
        self.requests = self.errors = 0

    async def book(self, symbol: str) -> OrderBook:
        cached = self._cache.get(symbol)
        if cached and self.clock.time() - cached.received_at <= self.cache_s:
            return cached
        async with self._lock:                                   # tope de pedidos por segundo (con ráfaga)
            now = self.clock.monotonic()
            if self._refill_at is not None:
                self._tokens = min(self.burst, self._tokens + (now - self._refill_at) * self.rate)
            self._refill_at = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self.rate)
                self._tokens = 1.0
                self._refill_at = self.clock.monotonic()
            self._tokens -= 1
        self.requests += 1
        try:
            async with self.session.get(self.url, params={"symbol": symbol, "limit": str(self.depth)},
                                        timeout=self.timeout) as r:
                MONITOR.observe(r.headers)                       # peso usado de la IP (marketdata/peso.py)
                if r.status != 200:
                    raise MarketDataError(f"{self.venue} {symbol}: HTTP {r.status}")
                data = await r.json(content_type=None)
        except MarketDataError:
            self.errors += 1
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            self.errors += 1
            raise MarketDataError(f"{self.venue} {symbol}: {type(e).__name__}: {redact(e)}") from e
        book = parse_depth(self.venue, symbol, data, self.clock.time())
        self._cache[symbol] = book
        return book
