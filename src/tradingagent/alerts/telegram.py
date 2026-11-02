"""Cliente asíncrono de la Bot API de Telegram (sendMessage, getMe, getUpdates) sobre aiohttp.

Se usa la API HTTP directamente en lugar de aiogram o python-telegram-bot: para mandar alertas alcanzan
tres métodos, y así se controla cada timeout, cada reintento y qué se registra (la URL lleva el token:
nunca se loguea sin redactar). La base es configurable para apuntar a un servidor de prueba o a un
servidor local de la Bot API.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass

import aiohttp

from ..core.redact import redact
from .model import Alert

log = logging.getLogger("tradingagent.telegram")
API = "https://api.telegram.org"


def api_base(env) -> str:
    """TELEGRAM_API_BASE permite un servidor propio de la Bot API (o uno de prueba)."""
    return (env.get("TELEGRAM_API_BASE") or API).rstrip("/")
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SendResult:
    ok: bool
    retry_after: float | None = None    # 429: segundos que pide Telegram
    permanent: bool = False             # token inválido, bot bloqueado o chat inexistente: no reintentar
    error: str = ""
    latency_ms: float = 0.0


class TelegramTransport:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, *, base_url: str = API, timeout_s: float = 10.0):
        if not token or not chat_id:
            raise ValueError("Telegram necesita TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
        self.token, self.chat_id = token, str(chat_id)
        self.base = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_s, connect=min(5.0, timeout_s))
        self.session: aiohttp.ClientSession | None = None
        self.last_updates: int | None = None       # cuántos mensajes devolvió el último getUpdates

    async def open(self) -> None:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _safe(self, e) -> str:
        return redact(e, secrets=(self.token,))[:300]

    async def call(self, method: str, payload: dict | None = None) -> tuple[dict | None, float, str]:
        """(respuesta JSON o None, latencia ms, error redactado)."""
        await self.open()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            async with self.session.post(f"{self.base}/bot{self.token}/{method}", json=payload or {}) as r:
                text = await r.text()
                ms = (loop.time() - t0) * 1000
                try:
                    data = json.loads(text)
                except ValueError:
                    return None, ms, f"HTTP {r.status} sin JSON"
                if not isinstance(data, dict):
                    return None, ms, f"HTTP {r.status} respuesta inesperada"
                data.setdefault("error_code", r.status if r.status >= 400 else None)
                return data, ms, ""
        except TimeoutError:
            return None, (loop.time() - t0) * 1000, "sin respuesta de Telegram (timeout)"
        except aiohttp.ClientError as e:
            return None, (loop.time() - t0) * 1000, f"{type(e).__name__}: {self._safe(e)}"

    async def get_me(self) -> SendResult:
        data, ms, err = await self.call("getMe")
        if data and data.get("ok"):
            u = data.get("result") or {}
            return SendResult(True, error=f"@{u.get('username', '?')}", latency_ms=ms)
        return self._failure(data, err, ms)

    async def send(self, alert: Alert) -> SendResult:
        payload = {"chat_id": self.chat_id, "text": alert.text, "parse_mode": "HTML",
                   "disable_web_page_preview": True, "disable_notification": alert.silent}
        data, ms, err = await self.call("sendMessage", payload)
        if data and data.get("ok"):
            return SendResult(True, latency_ms=ms)
        if data and data.get("error_code") == 400 and "parse" in str(data.get("description", "")).lower():
            plain = dict(payload, text=_TAG.sub("", alert.text))       # HTML rechazado: va como texto plano
            plain.pop("parse_mode")
            data, ms2, err = await self.call("sendMessage", plain)
            if data and data.get("ok"):
                return SendResult(True, latency_ms=ms + ms2)
        return self._failure(data, err, ms)

    def _failure(self, data, err, ms) -> SendResult:
        if data is None:
            return SendResult(False, error=err, latency_ms=ms)
        code = data.get("error_code")
        desc = self._safe(data.get("description", ""))
        if code == 429:
            ra = (data.get("parameters") or {}).get("retry_after")
            return SendResult(False, retry_after=float(ra) if ra is not None else 5.0, error=f"429 {desc}",
                              latency_ms=ms)
        if code in (401, 403, 404):
            return SendResult(False, permanent=True, error=f"{code} {desc}", latency_ms=ms)
        return SendResult(False, error=f"{code} {desc}", latency_ms=ms)

    async def recent_chats(self) -> list[dict]:
        """Chats que le escribieron al bot en las últimas 24 h (para averiguar TELEGRAM_CHAT_ID), con sus
        últimos textos. No confirma nada: lo leído sigue disponible para cualquier otro programa del bot."""
        data, _, err = await self.call("getUpdates", {"limit": 100, "timeout": 0})
        if not data or not data.get("ok"):
            raise RuntimeError(err or self._safe(data.get("description", "error")))
        updates = data.get("result") or []
        self.last_updates = len(updates)
        chats: dict = {}
        for upd in updates:
            msg = (upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
                   or upd.get("my_chat_member") or {})
            chat = msg.get("chat") or {}
            if "id" not in chat:
                continue
            c = chats.setdefault(chat["id"], {"id": chat["id"], "type": chat.get("type"), "texts": [],
                                              "name": chat.get("title") or chat.get("username") or chat.get("first_name")})
            if msg.get("text"):                                    # los eventos sin texto no tapan un /id anterior
                c["texts"] = [*c["texts"], str(msg["text"])[:100]][-10:]
        return list(chats.values())


class LogTransport:
    """Sin Telegram configurado: las alertas van al log (nunca se pierden en silencio)."""
    name = "log"

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_me(self) -> SendResult:
        return SendResult(True, error="(sin Telegram: alertas al log)")

    async def send(self, alert: Alert) -> SendResult:
        log.warning("ALERTA %s: %s", alert.kind, _TAG.sub("", alert.text).replace("\n", " | "))
        return SendResult(True)
