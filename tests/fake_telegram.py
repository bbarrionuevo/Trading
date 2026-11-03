"""Servidor HTTP real que imita la Bot API de Telegram, con fallas programables por pedido.

Modos (lista `script`, se consume un modo por pedido a sendMessage; vacío = "ok"):
  ok · 429:<segundos> · 500 · 401 · 400parse · hang (no responde hasta `release`, después acepta)
"""
import asyncio

from aiohttp import web

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmn"


class FakeTelegram:
    def __init__(self, token: str = TOKEN):
        self.token = token
        self.script: list[str] = []
        self.messages: list[dict] = []           # mensajes aceptados (como los vería el chat)
        self.requests: list[tuple[str, dict]] = []
        self.release = asyncio.Event()
        self.updates = [{"update_id": 1, "message": {"chat": {"id": 4242, "type": "private", "first_name": "Ana"}}}]

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_post("/bot{token}/{method}", self.handle)
        return a

    async def handle(self, request: web.Request) -> web.Response:
        if request.match_info["token"] != self.token:
            return web.json_response({"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401)
        method = request.match_info["method"]
        payload = await request.json()
        self.requests.append((method, payload))
        if method == "getMe":
            return web.json_response({"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_bot"}})
        if method == "getUpdates":                    # con offset: solo lo nuevo (como la API real)
            off = payload.get("offset")
            got = [u for u in self.updates if off is None or int(u["update_id"]) >= int(off)]
            return web.json_response({"ok": True, "result": got})
        mode = self.script.pop(0) if self.script else "ok"
        if mode == "hang":
            await self.release.wait()
        if mode.startswith("429"):
            ra = int(mode.split(":")[1]) if ":" in mode else 1
            return web.json_response({"ok": False, "error_code": 429, "description": f"Too Many Requests: retry after {ra}",
                                      "parameters": {"retry_after": ra}}, status=429)
        if mode == "500":
            return web.json_response({"ok": False, "error_code": 500, "description": "Internal"}, status=500)
        if mode == "401":
            return web.json_response({"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401)
        if mode == "400parse" and payload.get("parse_mode"):
            return web.json_response({"ok": False, "error_code": 400,
                                      "description": "Bad Request: can't parse entities"}, status=400)
        self.messages.append(payload)
        return web.json_response({"ok": True, "result": {"message_id": len(self.messages)}})
