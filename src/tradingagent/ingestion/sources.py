"""Fuentes por sondeo HTTP (RSS/Atom): una tarea por fuente, aislada de las demás.

- GET condicional (ETag / Last-Modified): si nada cambió el servidor responde 304 y no se descarga nada.
- Tope de tamaño leído en bloques y timeout total por pedido.
- Fallas: espera exponencial con jitter; 429 respeta Retry-After; tras `open_after` fallas seguidas el
  circuito se abre y solo se prueba una vez cada `cooldown_s` (no se martilla un servidor caído).
- El parseo corre en un hilo aparte (asyncio.to_thread): un feed grande no frena el bucle de eventos.
- La primera lectura de cada fuente es "historia" (backlog): se registra para deduplicar pero no se opera.
"""
import asyncio
import logging
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime

import aiohttp

from ..core.events import TIER_MEDIA, NewsItem, stable_id
from ..core.redact import redact
from .feeds import FeedEntry, FeedError, parse_feed
from .normalize import canonical_url, clean_text, parse_datetime

log = logging.getLogger("tradingagent.sources")
USER_AGENT = "tradingagent/0.1 (+https://github.com/bbarrionuevo/trading)"


@dataclass(frozen=True)
class SourceSpec:
    id: str
    url: str
    tier: int = TIER_MEDIA
    interval_s: float = 60.0
    timeout_s: float = 10.0
    max_age_s: float = 300.0             # más vieja que esto al llegar = stale (REGLAS B-2)
    category: str = "news"               # macro | regulatorio | cripto | hack | earnings | filings ...
    assets: tuple[str, ...] = ()
    headers: dict = field(default_factory=dict)
    requires_env: tuple[str, ...] = ()   # p. ej. SEC_USER_AGENT: sin eso la fuente no se habilita
    enabled: bool = True
    max_bytes: int = 5_000_000
    max_backoff_s: float = 900.0
    open_after: int = 5
    cooldown_s: float = 600.0
    note: str = ""
    format: str = "rss"                  # rss (RSS/Atom) o el lector JSON de ingestion/announcements.py
    os_trust: bool = False               # verificar el certificado con el almacén del sistema (truststore): Upbit


@dataclass
class SourceHealth:
    state: str = "nueva"                 # nueva | ok | degradada | abierta
    fetches: int = 0
    not_modified: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    items: int = 0
    last_ok: float | None = None
    last_error: str = ""
    last_latency_ms: float | None = None
    next_poll_in_s: float = 0.0

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class FetchError(Exception):
    def __init__(self, msg, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, IndexError):
        return None


class PollingSource:
    def __init__(self, spec: SourceSpec, session: aiohttp.ClientSession, clock, on_items, *,
                 parse=parse_feed, rng: random.Random | None = None, seen_max: int = 5000):
        if parse is parse_feed and spec.format != "rss":               # anuncios oficiales en JSON
            from .announcements import PARSERS
            parse = PARSERS[spec.format]
        self.spec, self.session, self.clock, self.on_items, self.parse = spec, session, clock, on_items, parse
        self.rng = rng or random.Random()
        self.health = SourceHealth()
        self._etag = self._last_modified = None
        self._seen: OrderedDict[str, None] = OrderedDict()     # entradas ya emitidas por ESTA fuente
        self._seen_max = seen_max
        self._first = True

    # -- estado entre corridas (recolector de corridas cortas) ---------------------------------
    def export_state(self, keep: int = 200) -> dict:
        """ETag, Last-Modified y lo ya visto (huellas de las últimas `keep` entradas) para la próxima corrida."""
        return {"etag": self._etag, "last_modified": self._last_modified, "seen": list(self._seen)[-keep:],
                "first_done": not self._first}

    def import_state(self, d: dict) -> None:
        self._etag, self._last_modified = d.get("etag"), d.get("last_modified")
        for k in (d.get("seen") or [])[-self._seen_max:]:
            self._seen[k] = None
        self._first = not d.get("first_done", False)

    # -- red ----------------------------------------------------------------------------------
    def _ssl(self):
        """Con `os_trust`, el almacén de certificados del sistema (en Windows completa cadenas que Python no)."""
        if not self.spec.os_trust:
            return True
        if not hasattr(self, "_os_ctx"):
            from ..research.probe import os_trust_ssl
            self._os_ctx = os_trust_ssl()
        return self._os_ctx or True

    async def fetch(self) -> bytes | None:
        """Cuerpo del feed, o None si no cambió (304). Lanza FetchError ante cualquier falla."""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
                   **self.spec.headers}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified
        t0 = self.clock.monotonic()
        try:
            async with self.session.get(self.spec.url, headers=headers, ssl=self._ssl(),
                                        timeout=aiohttp.ClientTimeout(total=self.spec.timeout_s)) as r:
                if r.status == 304:
                    return None
                if r.status == 429 or r.status >= 500:
                    raise FetchError(f"HTTP {r.status}", _retry_after(r.headers.get("Retry-After")))
                if r.status >= 400:
                    raise FetchError(f"HTTP {r.status}")
                chunks, size = [], 0
                async for chunk in r.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > self.spec.max_bytes:
                        raise FetchError(f"respuesta de más de {self.spec.max_bytes} bytes")
                    chunks.append(chunk)
                self._etag = r.headers.get("ETag") or self._etag
                self._last_modified = r.headers.get("Last-Modified") or self._last_modified
                return b"".join(chunks)
        except FetchError:
            raise
        except TimeoutError as e:
            raise FetchError(f"sin respuesta en {self.spec.timeout_s} s") from e
        except aiohttp.ClientError as e:
            raise FetchError(f"{type(e).__name__}: {redact(e)}") from e
        finally:
            self.health.last_latency_ms = round((self.clock.monotonic() - t0) * 1000, 1)

    # -- conversión ---------------------------------------------------------------------------
    def to_item(self, e: FeedEntry, received_at: float) -> NewsItem:
        url = canonical_url(e.link)
        key = e.guid or url or e.title
        assets = self.spec.assets
        return NewsItem(id=stable_id(self.spec.id, key), source=self.spec.id, tier=self.spec.tier,
                        kind="rss" if self.spec.format == "rss" else "api",
                        title=clean_text(e.title, 500), body=clean_text(e.summary), url=url,
                        published_at=parse_datetime(e.published), received_at=received_at,
                        categories=tuple(e.categories), assets_hint=assets, category_hint=self.spec.category,
                        guid=e.guid)

    def _new_entries(self, entries: list[FeedEntry]) -> list[FeedEntry]:
        fresh = []
        for e in entries:
            k = stable_id(e.guid or e.link or e.title, n=16)       # huella corta: el estado ocupa poco
            if k in self._seen:
                self._seen.move_to_end(k)                          # sigue en el feed: no se olvida
                continue
            self._seen[k] = None
            if len(self._seen) > self._seen_max:
                self._seen.popitem(last=False)
            fresh.append(e)
        return fresh

    async def poll_once(self) -> tuple[list[NewsItem], bool]:
        """(ítems nuevos, es_primera_lectura). Lanza FetchError/FeedError."""
        body = await self.fetch()
        self.health.fetches += 1
        if body is None:
            self.health.not_modified += 1
            return [], False
        entries = await asyncio.to_thread(self.parse, body)
        now = self.clock.time()
        items = [self.to_item(e, now) for e in self._new_entries(entries)]
        first, self._first = self._first, False
        return items, first

    # -- ciclo --------------------------------------------------------------------------------
    def _next_delay(self, retry_after: float | None = None) -> float:
        s, h = self.spec, self.health
        if h.consecutive_failures == 0:
            base = s.interval_s
        elif h.consecutive_failures >= s.open_after:
            h.state = "abierta"
            base = s.cooldown_s
        else:
            h.state = "degradada"
            base = min(s.max_backoff_s, s.interval_s * (2 ** h.consecutive_failures))
        if retry_after is not None:
            base = max(base, min(retry_after, s.max_backoff_s))
        return base * self.rng.uniform(0.85, 1.15)

    async def step(self) -> float:
        """Una lectura completa; devuelve cuánto esperar hasta la próxima."""
        retry_after = None
        try:
            items, first = await self.poll_once()
        except (FetchError, FeedError) as e:
            h = self.health
            h.failures += 1
            h.consecutive_failures += 1
            h.last_error = redact(e)[:300]
            retry_after = getattr(e, "retry_after", None)
            log.warning("fuente %s: %s (falla %d seguida)", self.spec.id, h.last_error, h.consecutive_failures)
        else:
            h = self.health
            h.state, h.consecutive_failures, h.last_ok = "ok", 0, self.clock.time()
            h.items += len(items)
            if items:
                await self.on_items(self.spec, items, first)
        delay = self._next_delay(retry_after)
        self.health.next_poll_in_s = round(delay, 1)
        return delay

    async def run(self, stop: asyncio.Event) -> None:
        await asyncio.sleep(self.rng.uniform(0, min(5.0, self.spec.interval_s / 4)))   # no todas a la vez
        while not stop.is_set():
            delay = await self.step()
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass


def with_overrides(spec: SourceSpec, **kw) -> SourceSpec:
    return replace(spec, **{k: v for k, v in kw.items() if v is not None})
