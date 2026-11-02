"""Receptor de noticias empujadas (webhooks): para feeds de baja latencia que se suscriben aparte.

Seguridad: escucha en 127.0.0.1 por defecto; cada pedido firmado con HMAC-SHA256 sobre
"<timestamp>.<cuerpo>" (cabeceras X-TA-Timestamp y X-TA-Signature: sha256=<hex>); se rechazan firmas
inválidas, relojes con más de `max_skew_s` de diferencia (repetición de pedidos viejos) y cuerpos de
más de `max_body` bytes. El nivel de confianza lo fija la configuración del remitente, nunca el cuerpo.
"""
import hashlib
import hmac
import json
import logging

from aiohttp import web

from ..core.events import TIER_UNVERIFIED, NewsItem, stable_id
from .normalize import canonical_url, clean_text, parse_datetime

log = logging.getLogger("tradingagent.webhook")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


class WebhookSource:
    def __init__(self, secret: str, on_item, clock, *, host: str = "127.0.0.1", port: int = 8787,
                 senders: dict[str, int] | None = None, max_body: int = 65536, max_skew_s: float = 300.0):
        if not secret or len(secret) < 16:
            raise ValueError("el webhook necesita un secreto de al menos 16 caracteres (WEBHOOK_SECRET)")
        self.secret, self.on_item, self.clock = secret, on_item, clock
        self.host, self.port, self.max_body, self.max_skew = host, port, max_body, max_skew_s
        self.senders = senders or {}
        self.counts = {"aceptados": 0, "firma_invalida": 0, "reloj": 0, "malformados": 0, "grandes": 0}
        self._runner: web.AppRunner | None = None
        self.bound_port: int | None = None

    def app(self) -> web.Application:
        a = web.Application(client_max_size=self.max_body)
        a.router.add_post("/news", self.handle)
        return a

    async def handle(self, request: web.Request) -> web.Response:
        try:
            body = await request.read()
        except web.HTTPRequestEntityTooLarge:
            self.counts["grandes"] += 1
            return web.json_response({"error": "cuerpo demasiado grande"}, status=413)
        ts = request.headers.get("X-TA-Timestamp", "")
        sig = request.headers.get("X-TA-Signature", "")
        if not hmac.compare_digest(sig, sign(self.secret, ts, body)):
            self.counts["firma_invalida"] += 1
            return web.json_response({"error": "firma inválida"}, status=401)
        try:
            skew = abs(self.clock.time() - float(ts))
        except ValueError:
            skew = float("inf")
        if skew > self.max_skew:
            self.counts["reloj"] += 1
            return web.json_response({"error": "timestamp fuera de ventana"}, status=401)
        try:
            d = json.loads(body)
            title = clean_text(str(d["title"]), 500)
            if not title:
                raise ValueError("título vacío")
        except (ValueError, KeyError, TypeError) as e:
            self.counts["malformados"] += 1
            return web.json_response({"error": f"cuerpo inválido: {type(e).__name__}"}, status=400)
        sender = request.headers.get("X-TA-Sender", "desconocido")[:40]
        url = canonical_url(str(d.get("url") or ""))
        item = NewsItem(
            id=stable_id(f"webhook.{sender}", str(d.get("id") or url or title)), source=f"webhook.{sender}",
            tier=self.senders.get(sender, TIER_UNVERIFIED), kind="webhook", title=title,
            body=clean_text(str(d.get("body") or "")), url=url,
            published_at=parse_datetime(str(d.get("published_at") or "")) if d.get("published_at") else None,
            received_at=self.clock.time(),
            assets_hint=tuple(str(x).upper()[:15] for x in (d.get("assets") or [])[:10]),
            category_hint=str(d.get("category") or "")[:30])
        self.counts["aceptados"] += 1
        await self.on_item(item)
        return web.json_response({"id": item.id}, status=202)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        addrs = self._runner.addresses
        self.bound_port = addrs[0][1] if addrs else self.port
        log.info("webhook escuchando en %s:%s", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
