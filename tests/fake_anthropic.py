"""Servidor HTTP real que imita POST /v1/messages de la API de Anthropic, con fallas programables.

Modos (lista `script`, uno por pedido; vacío = "ok"): ok · 429 · 500 · hang (no responde hasta `release`)
· refusal · badjson · cut (stop_reason max_tokens).
"""
import asyncio
import json

from aiohttp import web

KEY = "sk-ant-test-0000000000000000000000000000"


class FakeClaude:
    def __init__(self):
        self.script: list[str] = []
        self.reply = {"category": "hack", "polarity": -0.7, "certainty": 0.9, "assets": ["SOL"], "horizon_min": 120,
                      "new_event": True, "reason": "Robo confirmado en un exchange."}
        self.requests: list[dict] = []
        self.release = asyncio.Event()

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_post("/v1/messages", self.handle)
        return a

    async def handle(self, request: web.Request) -> web.Response:
        if KEY not in (request.headers.get("x-api-key"), request.headers.get("Authorization", "").removeprefix("Bearer ")):
            return web.json_response({"type": "error", "error": {"type": "authentication_error", "message": "bad"}},
                                     status=401)
        body = await request.json()
        self.requests.append(body)
        mode = self.script.pop(0) if self.script else "ok"
        if mode == "hang":
            await self.release.wait()
        if mode == "429":
            return web.json_response({"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
                                     status=429, headers={"retry-after": "1"})
        if mode == "500":
            return web.json_response({"type": "error", "error": {"type": "api_error", "message": "boom"}}, status=500)
        text = "{no es json" if mode == "badjson" else json.dumps(self.reply)
        stop = {"refusal": "refusal", "cut": "max_tokens"}.get(mode, "end_turn")
        return web.json_response({
            "id": f"msg_{len(self.requests)}", "type": "message", "role": "assistant", "model": body["model"],
            "content": [] if mode == "refusal" else [{"type": "text", "text": text}], "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 400, "output_tokens": 60}})
