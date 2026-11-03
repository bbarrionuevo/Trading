import contextlib

import pytest
from aiohttp import web

from tradingagent.core.clock import ManualClock


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Cada test con su TRADING_HOME aislado: nunca toca la carpeta real del usuario."""
    monkeypatch.setenv("TRADING_HOME", str(tmp_path / "home"))
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SEC_USER_AGENT", "WEBHOOK_SECRET", "ANTHROPIC_API_KEY",
              "CLAUDE_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "MEXC_ACCESS_KEY", "MEXC_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)


@contextlib.asynccontextmanager
async def serve(app: web.Application):
    """Servidor HTTP real en 127.0.0.1 con puerto libre. Devuelve la URL base."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        await runner.cleanup()


class JournalSpy:
    def __init__(self):
        self.records = []

    def record(self, kind, data=None, **kw):
        self.records.append((kind, data if data is not None else kw))
