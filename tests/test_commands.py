"""Órdenes del dueño por Telegram: solo su chat, nada viejo, nada que agregue riesgo."""
import aiohttp

from conftest import serve
from fake_telegram import TOKEN, FakeTelegram
from tradingagent import paths
from tradingagent.alerts.commands import Commands
from tradingagent.alerts.telegram import TelegramTransport

OWNER, OTHER = 777, 999


def msg(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "text": text}}


class Actions:
    def __init__(self):
        self.closed = []

    async def status_text(self):
        return "📊 Estado\nPráctica: 120,00 USDT"

    async def disarm(self):
        return "🔒 desarmado"

    async def close_all(self, why):
        self.closed.append(why)
        return "✅ Cerré todo"


async def test_ordenes_solo_del_dueno_sin_ejecutar_lo_viejo_y_nunca_armar(tmp_path):
    tg = FakeTelegram()
    tg.updates = [msg(1, OWNER, "/cerrar SI")]                 # quedó de antes de arrancar: no se ejecuta
    home = tmp_path / "home"
    act = Actions()
    async with serve(tg.app()) as url:
        t = TelegramTransport(TOKEN, str(OWNER), base_url=url)
        try:
            c = Commands(t, home, act, chat_id=str(OWNER))
            assert await c.poll_once(timeout_s=0) == 0 and act.closed == [] and tg.messages == []
            assert paths.read_json(home / "state" / "telegram-offset.json", {})["offset"] == 2
            tg.updates += [msg(2, OTHER, "/cerrar SI"), msg(3, OWNER, "/estado"), msg(4, OWNER, "/pausa"),
                           msg(5, OWNER, "/cerrar"), msg(6, OWNER, "/armar 100"), msg(7, OWNER, "hola")]
            assert await c.poll_once(timeout_s=0) == 4                  # el otro chat y el texto suelto, no
            texts = [m["text"] for m in tg.messages]
            assert all(m["chat_id"] == str(OWNER) for m in tg.messages)
            assert "Práctica: 120,00 USDT" in texts[0] and (home / "KILL").exists()
            assert "mandá exactamente: /cerrar SI" in texts[2] and act.closed == []      # pide confirmación
            assert "Armar dinero real no se puede desde acá" in texts[3]                 # /armar: solo la ayuda
            tg.updates += [msg(8, OWNER, "/seguir"), msg(9, OWNER, "/cerrar SI"), msg(10, OWNER, "/desarmar")]
            assert await c.poll_once(timeout_s=0) == 3
            assert not (home / "KILL").exists() and act.closed == ["pedido del dueño por Telegram"]
            assert "Cerré todo" in tg.messages[-2]["text"] and "desarmado" in tg.messages[-1]["text"]
            again = Commands(t, home, act, chat_id=str(OWNER))           # reinicio: retoma donde quedó
            assert again.offset == 11 and await again.poll_once(timeout_s=0) == 0
        finally:
            await t.close()


async def test_el_dueno_cierra_todo_lo_real_desde_el_celular(tmp_path):
    """De punta a punta con el operador real del simulacro: /cerrar SI cierra la posición y desarma."""
    import time

    from fake_anthropic import KEY, FakeClaude
    from fake_mexc_trade import FakeMexcTrade
    from test_live import HACK, Alerts, news
    from tradingagent import limits as limits_mod
    from tradingagent.app import OwnerActions
    from tradingagent.core.clock import ManualClock
    from tradingagent.execution.live import arming
    from tradingagent.execution.live.dryrun import DryRunClient
    from tradingagent.execution.live.venue import RealVenue
    from tradingagent.intelligence.llm import LlmClassifier
    from tradingagent.live import LiveTrader
    from tradingagent.research.futures import MexcFutures

    lim = limits_mod.load()
    clock = ManualClock(1793700000 + 600)
    prices = {"BTC_USDT": 20_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}
    fake = FakeMexcTrade(lambda sym, m: prices[sym], clock)
    claude = FakeClaude()
    claude.reply = {**claude.reply, "assets": ["BTC"], "polarity": -0.9, "certainty": 0.95}
    home = tmp_path / "home"
    arming.arm(home, 2, "", "", simulacro=True, capital=100, now=time.time())
    base = home / "simulacro" / "mexc-futuros"
    async with serve(fake.app()) as url, serve(claude.app()) as aurl, aiohttp.ClientSession() as s:
        mk = MexcFutures(base=url, max_leverage=1)
        mk.label = "SIMULACRO futuros MEXC"

        def venue(registry, books, pf, clk):
            return RealVenue(DryRunClient(books, s, capital=100, clock=clk, base=url), s, registry, books, pf, clk,
                             home=home, access_key=None, limits=lim, base=url)

        llm = LlmClassifier(KEY, base_url=aurl, clock=clock)
        try:
            t = LiveTrader(mk, s, clock, lim, alerts=Alerts(), home=home, capital=100.0, venue_factory=venue,
                           base_dir=base, real=True, simulated=True, llm=llm)
            await t.start()
            await t.on_news(news("h1", HACK, clock.time()))
            assert t.st["positions"]

            class FakeAgent:
                trader, real_trader, env = None, t, {}

            act = OwnerActions(FakeAgent())
            assert "Simulacro" in await act.status_text() and "corto BTC" in await act.status_text()
            clock.advance(2)
            out = await act.close_all("pedido del dueño por Telegram")
            assert "Cerré todo (1 posiciones)" in out and t.st["positions"] == {}
            assert not (home / "ARMADO.json").exists()                          # quedó desarmado
            assert t.counts.get("pedido_del_dueno") == 1
        finally:
            await llm.close()
