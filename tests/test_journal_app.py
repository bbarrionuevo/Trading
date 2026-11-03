import asyncio
import json
import time
from decimal import Decimal as D

import pytest
from aiohttp import web

from conftest import serve
from fake_telegram import TOKEN, FakeTelegram
from tradingagent import config, paths
from tradingagent.__main__ import main as cli
from tradingagent.app import Agent, build_sources
from tradingagent.core.clock import ManualClock
from tradingagent.execution.orders import OrderIntent, Side
from tradingagent.ingestion.sources import SourceSpec
from tradingagent.marketdata.book import OrderBook
from tradingagent.telemetry.journal import Journal, verify


def test_diario_encadenado_detecta_alteraciones_y_sigue_tras_reinicio(tmp_path):
    clock = ManualClock()
    j = Journal(tmp_path, clock).start()
    for i in range(5):
        j.record("news", {"i": i, "title": f"t{i}"})
    clock.advance(86400)                                         # cambia el día: otro archivo, misma cadena
    j.record("order", {"i": 5})
    j.flush()
    j.stop()
    assert verify(tmp_path) == (True, 6, "")
    j2 = Journal(tmp_path, clock).start()                        # reinicio: continúa la cadena
    j2.record("start", {})
    j2.flush()
    j2.stop()
    assert verify(tmp_path)[:2] == (True, 7) and len(list(tmp_path.glob("journal-*.jsonl"))) == 2
    f = sorted(tmp_path.glob("journal-*.jsonl"))[0]
    lines = f.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["data"]["title"] = "alterado"
    lines[2] = json.dumps(rec)
    f.write_text("\n".join(lines) + "\n")
    ok, n, problem = verify(tmp_path)
    assert not ok and n == 2 and "se rompe" in problem
    lines = f.read_text().splitlines()
    del lines[1]                                                 # borrar una línea también se detecta
    f.write_text("\n".join(lines) + "\n")
    assert not verify(tmp_path)[0]


def test_diario_lleno_cuenta_la_perdida_y_no_bloquea(tmp_path):
    j = Journal(tmp_path, max_queue=10)                          # sin hilo: nadie vacía la cola
    t0 = time.perf_counter()
    results = [j.record("x", {"i": i}) for i in range(100)]
    assert time.perf_counter() - t0 < 0.05 and results.count(True) == 10 and j.dropped == 90


def test_configuracion_valida_y_rechaza_lo_fuera_de_rango(tmp_path):
    cfg = config.load(None)
    assert cfg["mode"] == "paper" and config.validate(cfg) == []
    p = tmp_path / "c.yaml"
    p.write_text("mode: live\npaper: {participation: 3}\n")
    with pytest.raises(ValueError) as e:
        config.load(p)
    assert "solo opera en paper" in str(e.value) and "participation" in str(e.value)
    p.write_text("ingestion: {sources: {coindesk: {enabled: false}}}\n")
    enabled, off = build_sources(config.load(p), {})
    assert off["coindesk"] == "apagada en la configuración"
    assert "falta SEC_USER_AGENT" in off["sec.press"] and "fed.press" in {s.id for s in enabled}
    enabled, off = build_sources(config.load(None), {"SEC_USER_AGENT": "Nombre contacto@example.org"})
    sec = next(s for s in enabled if s.id == "sec.press")
    assert sec.headers["User-Agent"] == "Nombre contacto@example.org"


class FreshBooks:
    """Libro de BTCUSDT siempre fresco (como un proveedor en vivo)."""

    async def book(self, symbol):
        now = time.time()
        return OrderBook.from_levels("t", symbol, [("64990", "2")], [("65000", "2")], now, now)


async def test_agente_completo_arranca_avisa_lee_opera_late_y_cierra(tmp_path, capsys):
    fake = FakeTelegram()
    t_pub = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
    state = {"n": 0}

    async def feed(request):
        state["n"] += 1
        items = "<item><title>Old archive story about markets today</title><guid>old</guid></item>"
        if state["n"] > 1:                                     # después de la historia, llega una noticia nueva
            items += (f"<item><title>SEC approves spot Solana ETF applications</title><guid>new</guid>"
                      f"<pubDate>{t_pub}</pubDate></item>")
        return web.Response(text=f"<rss><channel>{items}</channel></rss>", content_type="application/rss+xml")

    rss = web.Application()
    rss.router.add_get("/rss", feed)
    async with serve(fake.app()) as tg_url, serve(rss) as rss_url:
        cfg = config.load(None)
        cfg["heartbeat_s"] = 2
        env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "777"}
        lines = []
        cfg["trader"]["enabled"] = False                         # sin mercado real en este test
        agent = Agent(cfg, env, sources=[SourceSpec("prueba", rss_url + "/rss", interval_s=0.3, max_age_s=300)],
                      book_provider=FreshBooks(), telegram_base_url=tg_url, printer=lines.append)
        run = asyncio.create_task(agent.run())
        for _ in range(300):
            if any("SEC approves" in x for x in lines) and getattr(agent, "execution", None):
                break
            await asyncio.sleep(0.02)
        ok, why = agent.execution.submit(OrderIntent("t-1", "BTCUSDT", Side.BUY, notional=D("500"),
                                                     max_slippage_bps=D("20"),
                                                     tags={"role": "entry", "why": "ETF de SOL aprobado", "impact": 0.6}))
        assert ok, why
        for _ in range(300):
            if len(fake.messages) >= 2 and any(x.startswith("♥") for x in lines):
                break
            await asyncio.sleep(0.02)
        agent.stop()
        await asyncio.wait_for(run, 20)
    texts = [m["text"] for m in fake.messages]
    assert "TRADING está funcionando" in texts[0]                          # mensaje de prueba al inicializar
    assert any("Compré BTC" in t and "ETF de SOL aprobado" in t for t in texts)
    assert any("SEC approves spot Solana ETF" in x for x in lines)         # noticia nueva: aceptada
    assert not any("Old archive story" in x for x in lines)               # la historia no se opera
    hb = json.loads(paths.state("heartbeat.json").read_text())
    assert hb["sources"]["total"] == 1 and hb["news"]["aceptadas"] == 1 and hb["alerts"]["health"] == "ok"
    assert hb["loop_lag"]["p99_ms"] < 50
    ok, n, problem = verify(paths.home() / "journal")
    kinds = [json.loads(x)["kind"] for f in sorted((paths.home() / "journal").glob("*.jsonl"))
             for x in f.read_text().splitlines()]
    assert ok and kinds[0] == "start" and kinds[-1] == "stop" and "news" in kinds and "order" in kinds


async def test_cli_telegram_test_y_chats_contra_el_servidor_de_prueba(monkeypatch, capsys):
    fake = FakeTelegram()
    async with serve(fake.app()) as url:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
        monkeypatch.setenv("TELEGRAM_API_BASE", url)
        assert await asyncio.to_thread(cli, ["telegram-test"]) == 0
        assert await asyncio.to_thread(cli, ["telegram-chats"]) == 0
    out = capsys.readouterr().out
    assert "@test_bot OK" in out and "Mensaje de prueba enviado." in out and "TELEGRAM_CHAT_ID=4242" in out
    assert "Prueba de TRADING" in fake.messages[0]["text"] and TOKEN not in out


def test_cli_sin_credenciales_y_diario_vacio(monkeypatch, capsys):
    assert cli(["telegram-test"]) == 2
    assert cli(["journal-verify"]) == 0
    assert cli(["sources"]) == 0
    out = capsys.readouterr().out
    assert "Faltan TELEGRAM_BOT_TOKEN" in out and "ÍNTEGRO" in out and "OFF  sec.press" in out


def test_harness_de_escenario_ejemplo():
    from pathlib import Path

    from tradingagent.execution.harness import run_scenario
    path = Path(__file__).resolve().parents[1] / "examples" / "escenario_noticia.jsonl"
    rep = asyncio.run(run_scenario(path))
    by_id = {o["client_id"]: o for o in rep["orders"]}
    assert by_id["n1-entrada"]["status"] == "filled" and float(by_id["n1-entrada"]["slippage_bps"]) > 0
    assert by_id["n1-tp"]["status"] == "filled" and by_id["n1-tp"]["avg_price"] == "66330"
    assert by_id["n1-stop"]["status"] == "canceled" and "oco" in by_id["n1-stop"]["reason"]


async def test_cancelar_al_agente_como_ctrl_c_en_windows_cierra_en_orden():
    cfg = config.load(None)
    cfg["trader"]["enabled"] = False
    agent = Agent(cfg, {}, sources=[], book_provider=FreshBooks(), printer=lambda *_: None)
    run = asyncio.create_task(agent.run())
    for _ in range(200):
        if getattr(agent, "execution", None):
            break
        await asyncio.sleep(0.01)
    t0 = time.monotonic()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert time.monotonic() - t0 < 8
    agent.journal.flush()
    kinds = [json.loads(x)["kind"] for f in sorted((paths.home() / "journal").glob("*.jsonl"))
             for x in f.read_text().splitlines()]
    assert kinds[0] == "start" and kinds[-1] == "stop" and verify(paths.home() / "journal")[0]
