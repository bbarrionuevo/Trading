"""Anuncios OFICIALES de Binance empujados por WebSocket (`wss://api.binance.com/sapi/wss`, tema
`com_announcement_en`, documentación oficial): llegan cuando Binance publica, sin esperar al sondeo ni a la copia
del CDN, y de TODOS los catálogos (listados, deslistados, lanzamientos de futuros, noticias), no solo del 48.

- Firma con una clave de Binance de **SOLO LECTURA** (`BINANCE_LECTURA_KEY` / `BINANCE_LECTURA_SECRET`), HMAC-SHA256
  sobre `random=…&topic=…&recvWindow=…&timestamp=…`. Nunca la clave de trading ni rutas de órdenes (REGLAS A-3: las
  órdenes solo salen de execution/live/; lo fija `test_a3`).
- Cada conexión dura hasta 24 h: se renueva sola a las 23 h. Si se corta, vuelve a conectar con espera creciente
  (5 s → 2 min). PING cada 25 s (Binance corta si no recibe uno por minuto) y casi ningún mensaje propio (el tope es
  5 por segundo; muchas reconexiones seguidas pueden bloquear la IP: de ahí la espera).
- Cada anuncio entra por la misma ingesta que el resto (deduplicado, frescura, ruido): si el sondeo de listados ya lo
  trajo, este queda como duplicado, y al revés. Probado desde la PC el 4-nov: conecta en ~1,2 s y acepta el tema.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import aiohttp

from ..core.events import TIER_OFFICIAL, NewsItem, stable_id
from ..core.redact import redact
from .normalize import clean_text
from .sources import SourceHealth

log = logging.getLogger("tradingagent.binance_ws")
WSS = "wss://api.binance.com/sapi/wss"
TIME_URL = "https://api.binance.com/api/v3/time"
TOPIC = "com_announcement_en"
SOURCE_ID = "binance.ws"
RENEW_S = 23 * 3600
PING_S = 25.0
BACKOFF_S = (5, 10, 20, 40, 80, 120)
# Catálogos de Binance → pista de categoría (el clasificador y la IA deciden por el texto). Los 7 que trae la lista sin
# catálogo (7-nov: 48, 49, 51, 93, 128, 157 y 161) y el 50. Uno que no esté acá se avisa una vez por Telegram.
CATALOGS = {48: "listado", 161: "deslistado", 49: "noticias", 50: "listado", 51: "api", 93: "actividad",
            128: "airdrop", 157: "mantenimiento"}
NEW_CATALOGS_MAX = 50          # por si el canal manda basura: no se anotan más que estos


class BinanceAnnouncements:
    """Fuente empujada: mantiene la conexión y entrega cada anuncio a `on_item(item, max_age_s=...)`."""

    def __init__(self, key: str, secret: str, on_item, clock, *, url: str = WSS, time_url: str = TIME_URL,
                 max_age_s: float = 600.0, backoff_s=BACKOFF_S, renew_s: float = RENEW_S, on_new_catalog=None):
        if not key or not secret:
            raise ValueError("faltan BINANCE_LECTURA_KEY y BINANCE_LECTURA_SECRET")
        self._key, self._secret = key, secret
        self.on_item, self.clock, self.url, self.time_url = on_item, clock, url, time_url
        self.max_age_s, self.backoff_s, self.renew_s = max_age_s, tuple(backoff_s), renew_s
        self.health = SourceHealth()
        self.connected = False
        self.counts = {"anuncios": 0, "conexiones": 0, "cortes": 0}
        self.on_new_catalog = on_new_catalog            # (número, nombre, título) de un catálogo que no está en CATALOGS
        self.new_catalogs: set = set()

    def snapshot(self) -> dict:
        return {"conectada": self.connected, **self.counts, "estado": self.health.state,
                "ultimo_error": self.health.last_error}

    def signed_url(self, offset_ms: int = 0) -> str:
        q = urlencode({"random": secrets.token_hex(16), "topic": TOPIC, "recvWindow": 30000,
                       "timestamp": int(self.clock.time() * 1000) + offset_ms})
        return f"{self.url}?{q}&signature={hmac.new(self._secret.encode(), q.encode(), hashlib.sha256).hexdigest()}"

    def parse(self, raw: str) -> NewsItem | None:
        """Un mensaje del canal → la noticia (o None si es una respuesta a un comando)."""
        d = json.loads(raw)
        if not isinstance(d, dict) or d.get("type") != "DATA":
            if isinstance(d, dict) and d.get("type") == "COMMAND" and d.get("code") not in (None, "00000000"):
                log.warning("Binance rechazó %s del canal de anuncios: %s", d.get("subType"), str(d)[:160])
            return None
        a = json.loads(d["data"]) if isinstance(d.get("data"), str) else (d.get("data") or {})
        title = clean_text(str(a.get("title") or ""), 500)
        if not title:
            return None
        pub = a.get("publishDate")
        cat = a.get("catalogId")
        if cat not in CATALOGS:
            self._seen_new_catalog(cat, a.get("catalogName"), title)
        return NewsItem(id=stable_id(SOURCE_ID, f"{cat}:{title}:{pub}"), source=SOURCE_ID, tier=TIER_OFFICIAL,
                        kind="websocket", title=title, body=clean_text(str(a.get("body") or ""))[:2000],
                        published_at=float(pub) / 1000 if pub else None, received_at=self.clock.time(),
                        category_hint=CATALOGS.get(cat, clean_text(str(a.get("catalogName") or ""), 30)))

    def _seen_new_catalog(self, cat, name, title: str) -> None:
        """Un catálogo que no conocemos (pedido del dueño, 7-nov: avisar las categorías nuevas): una vez por catálogo,
        DESPUÉS de entregar el anuncio (call_soon: no le suma nada a la entrada). El anuncio sigue su camino como
        cualquier otro: lo decide la IA por el texto."""
        if cat in self.new_catalogs or len(self.new_catalogs) >= NEW_CATALOGS_MAX:
            return
        self.new_catalogs.add(cat)
        try:
            asyncio.get_running_loop().call_soon(self._new_catalog, cat, clean_text(str(name or ""), 60), title)
        except RuntimeError:                                  # sin bucle andando (pruebas): ya
            self._new_catalog(cat, clean_text(str(name or ""), 60), title)

    def _new_catalog(self, cat, name: str, title: str) -> None:
        log.warning("catálogo de Binance que no conocía: %s «%s» (primer anuncio: %s)", cat, name, title[:100])
        if self.on_new_catalog is not None:
            try:
                self.on_new_catalog(cat, name, title)
            except Exception:                                  # un aviso nunca corta el canal
                log.exception("no pude avisar el catálogo nuevo %s", cat)

    async def _offset(self, session) -> int:
        try:
            async with session.get(self.time_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return int((await r.json(content_type=None))["serverTime"]) - int(self.clock.time() * 1000)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError):
            return 0                                          # sin la hora del servidor, la propia (recvWindow 30 s)

    async def _connect_once(self, session, stop: asyncio.Event) -> None:
        offset = await self._offset(session)
        async with session.ws_connect(self.signed_url(offset), headers={"X-MBX-APIKEY": self._key},
                                      heartbeat=PING_S, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
            await ws.send_json({"command": "SUBSCRIBE", "value": TOPIC})
            self.connected = True
            self.counts["conexiones"] += 1
            self.health.state, self.health.last_ok, self.health.consecutive_failures = "ok", self.clock.time(), 0
            log.info("canal de anuncios de Binance conectado")
            started = time.monotonic()
            while not stop.is_set() and time.monotonic() - started < self.renew_s:
                try:
                    msg = await ws.receive(timeout=5.0)
                except TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        item = self.parse(msg.data)
                    except (ValueError, KeyError, TypeError) as e:
                        log.warning("mensaje raro del canal de anuncios: %s", str(e)[:120])
                        continue
                    if item is not None:
                        self.counts["anuncios"] += 1
                        self.health.items += 1
                        self.health.last_ok = self.clock.time()
                        await self.on_item(item, max_age_s=self.max_age_s)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                  aiohttp.WSMsgType.ERROR):
                    raise ConnectionError(f"Binance cerró el canal de anuncios ({msg.type.name})")

    async def run(self, stop: asyncio.Event, session) -> None:
        attempt = 0
        while not stop.is_set():
            try:
                await self._connect_once(session, stop)
                attempt = 0                                   # se renovó a las 23 h: vuelve enseguida
                delay = 0.0
            except Exception as e:                                       # noqa: BLE001 (la fuente nunca se cae)
                self.counts["cortes"] += 1
                h = self.health
                h.failures += 1
                h.consecutive_failures += 1
                h.last_error = redact(f"{type(e).__name__}: {e}", secrets=(self._key, self._secret))[:200]
                h.state = "degradada" if h.consecutive_failures < 3 else "abierta"
                delay = self.backoff_s[min(attempt, len(self.backoff_s) - 1)]
                attempt += 1
                log.warning("canal de anuncios de Binance cortado (%s): reintento en %.0f s", h.last_error, delay)
            finally:
                self.connected = False
            if delay:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass
