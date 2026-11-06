import asyncio
import logging
import time

from conftest import serve
from fake_telegram import TOKEN, FakeTelegram
from tradingagent.alerts.dispatcher import AlertDispatcher
from tradingagent.alerts.model import (
    MAX_TELEGRAM_CHARS,
    Alert,
    Priority,
    circuit_breaker_alert,
    risk_alert,
    startup_alert,
    trade_alert,
)
from tradingagent.alerts.telegram import TelegramTransport
from tradingagent.core.looplag import LoopLagMonitor, percentile
from tradingagent.core.redact import redact


async def wait_until(cond, limit_s=5.0):
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


def make(url, token=TOKEN, timeout_s=2.0, **kw):
    kw.setdefault("rate_per_s", 100)
    kw.setdefault("burst", 100)
    kw.setdefault("retry_base_s", 0.05)
    return AlertDispatcher(lambda: TelegramTransport(token, "777", base_url=url, timeout_s=timeout_s), **kw)


def A(p, text, key=""):
    return Alert(Priority(p), "test", text, key=key)


async def test_al_iniciar_valida_el_bot_y_manda_el_mensaje_de_prueba():
    fake = FakeTelegram()
    async with serve(fake.app()) as url:
        d = make(url, startup_alert=startup_alert("PAPER", 6, 8, "0.1.0", extra="Símbolos: BTCUSDT"))
        await asyncio.to_thread(d.start)
        assert await wait_until(d.startup_done.is_set)
        await asyncio.to_thread(d.stop)
    assert d.startup["ok"] and d.startup["bot"] == "@test_bot" and d.health == "ok"
    assert [m for m, _ in fake.requests][:2] == ["getMe", "sendMessage"]
    msg = fake.messages[0]
    assert "TRADING está funcionando" in msg["text"] and "6 de 8 fuentes" in msg["text"] and msg["parse_mode"] == "HTML"
    assert msg["chat_id"] == "777"


async def test_riesgo_sale_primero_aunque_haya_llegado_ultimo():
    fake = FakeTelegram()
    fake.script = ["hang"]                                     # el primer envío queda colgado
    async with serve(fake.app()) as url:
        d = make(url)
        await asyncio.to_thread(d.start)
        d.submit(A(Priority.INFO, "i1"))
        assert await wait_until(lambda: len(fake.requests) >= 2)
        d.submit(A(Priority.SYSTEM, "s1"))
        d.submit(A(Priority.TRADE, "t1"))
        d.submit(A(Priority.RISK, "r1"))
        fake.release.set()
        assert await wait_until(lambda: len(fake.messages) == 4)
        await asyncio.to_thread(d.stop)
    assert [m["text"] for m in fake.messages] == ["i1", "r1", "t1", "s1"]


async def test_429_espera_lo_que_pide_telegram_y_reenvia():
    fake = FakeTelegram()
    fake.script = ["429:1"]
    async with serve(fake.app()) as url:
        d = make(url)
        await asyncio.to_thread(d.start)
        t0 = time.monotonic()
        d.submit(A(Priority.TRADE, "t1"))
        assert await wait_until(lambda: len(fake.messages) == 1, limit_s=6)
        elapsed = time.monotonic() - t0
        await asyncio.to_thread(d.stop)
    assert elapsed >= 1.0 and d.stats["429"] == 1 and d.stats["enviadas"] == 1


async def test_5xx_y_timeout_se_reintentan_con_espera():
    fake = FakeTelegram()
    fake.script = ["500", "hang"]
    async with serve(fake.app()) as url:
        d = make(url, timeout_s=0.3)
        await asyncio.to_thread(d.start)
        d.submit(A(Priority.RISK, "r1"))
        assert await wait_until(lambda: len(fake.messages) == 1, limit_s=6)
        fake.release.set()
        await asyncio.to_thread(d.stop)
    assert d.stats["fallidas"] == 2 and fake.messages[0]["text"] == "r1"


async def test_credenciales_invalidas_no_martillan_y_no_filtran_el_token():
    fake = FakeTelegram()
    bad = "999999999:BADtokenBADtokenBADtokenBADtokenBAD12"
    async with serve(fake.app()) as url:
        d = make(url, token=bad, permanent_pause_s=60, startup_alert=startup_alert("PAPER", 1, 1, "0"))
        await asyncio.to_thread(d.start)
        assert await wait_until(d.startup_done.is_set)
        d.submit(A(Priority.RISK, "r1"))
        await asyncio.sleep(0.6)
        await asyncio.to_thread(d.stop, 0.2)
    assert not d.startup["ok"] and d.health == "credenciales_invalidas"
    assert fake.messages == [] and len(fake.requests) <= 3          # pausa: no reintenta en bucle
    assert bad not in str(d.snapshot()) and "999999999:BAD" not in d.last_error


async def test_html_rechazado_se_reenvia_como_texto_plano():
    fake = FakeTelegram()
    fake.script = ["400parse"]
    async with serve(fake.app()) as url:
        d = make(url)
        await asyncio.to_thread(d.start)
        d.submit(A(Priority.TRADE, "<b>LONG</b> BTC"))
        assert await wait_until(lambda: len(fake.messages) == 1)
        await asyncio.to_thread(d.stop)
    assert fake.messages[0]["text"] == "LONG BTC" and "parse_mode" not in fake.messages[0]


async def test_telegram_colgado_no_frena_al_motor():
    """El motor encola 20.000 alertas con Telegram colgado: cada submit es de microsegundos, el bucle
    del motor no pierde ritmo y la cola queda acotada sin perder alertas de riesgo."""
    fake = FakeTelegram()
    fake.script = ["hang"] * 100_000
    async with serve(fake.app()) as url:
        d = make(url, timeout_s=30, max_queue=200)
        await asyncio.to_thread(d.start)
        assert await wait_until(d.startup_done.is_set)
        lag = LoopLagMonitor(interval=0.005)
        lag_task = asyncio.create_task(lag.run())
        await asyncio.sleep(0.2)
        times = []
        for i in range(20_000):
            t0 = time.perf_counter_ns()
            d.submit(A(Priority.RISK if i % 100 == 0 else Priority.TRADE, f"a{i}"))
            times.append(time.perf_counter_ns() - t0)
            if i % 200 == 0:
                await asyncio.sleep(0)                            # el motor sigue con su trabajo
        await asyncio.sleep(0.3)
        lag_task.cancel()
        snap = d.snapshot()
        fake.release.set()
        await asyncio.to_thread(d.stop, 0.5)
    p99_us, max_ms = percentile(times, 0.99) / 1000, max(times) / 1e6
    print(f"\nsubmit p99 {p99_us:.1f} µs · máx {max_ms:.2f} ms · lag del bucle {lag.stats()}")
    assert p99_us < 100, p99_us
    # El atraso se mide con el reloj de asyncio: en Windows salta de a ~15,6 ms, así que uno o dos saltos
    # son el reloj y no el motor (en Linux la resolución es de nanosegundos y el tope queda en 15 ms).
    tick_ms = time.get_clock_info("monotonic").resolution * 1000
    assert lag.stats()["p99_ms"] < 15 + 2 * tick_ms, (lag.stats(), tick_ms)
    assert snap["queued"] <= 400                                   # acotada (las RISK pueden pasar el tope blando)
    assert snap["stats"]["descartadas_por_espacio"] > 0
    risks_dropped = 200 - sum(1 for t in range(0, 20_000, 100))    # 200 RISK enviadas al motor, ninguna perdida
    assert risks_dropped == 0


def test_cola_llena_descarta_lo_menos_urgente_y_fusiona_por_clave():
    d = AlertDispatcher(lambda: None, max_queue=3)                 # sin hilo: solo la cola
    assert d.submit(A(Priority.INFO, "i1")) and d.submit(A(Priority.INFO, "i2")) and d.submit(A(Priority.TRADE, "t1"))
    assert d.submit(A(Priority.TRADE, "t2"))                       # desplaza la INFO más nueva
    assert not d.submit(A(Priority.INFO, "i3"))                    # la nueva es la menos urgente: se descarta
    assert d.submit(A(Priority.RISK, "r1")) and d.queued() == 4    # RISK pasa aunque esté llena
    for i in range(10):
        d.submit(A(Priority.RISK, f"r{i + 2}"))
    assert d.queued() == 6                                         # tope duro 2 × max_queue
    order = []
    while (it := d._pop()) is not None:
        order.append(it.alert.text)
    assert order[:5] == ["r1", "r2", "r3", "t1", "t2"] and "i2" not in order and order[-1] == "i1"
    d2 = AlertDispatcher(lambda: None, max_queue=10)
    d2.submit(A(Priority.SYSTEM, "fuente caída (1)", key="src"))
    d2.submit(A(Priority.SYSTEM, "fuente caída (2)", key="src"))
    assert d2.queued() == 1 and d2._pop().alert.text == "fuente caída (2)" and d2.stats["fusionadas"] == 1


def test_un_hilo_de_alertas_roto_nunca_rompe_a_quien_envia():
    def boom():
        raise RuntimeError("transporte roto")

    d = AlertDispatcher(boom)
    d.start()
    assert d.startup_done.wait(2)
    t0 = time.perf_counter()
    assert d.submit(A(Priority.RISK, "r1")) in (True, False)
    assert time.perf_counter() - t0 < 0.01 and d.health == "caido"
    d.stop(0.1)


def test_formato_de_alertas_escapado_y_acotado():
    t = trade_alert("BTCUSDT", "buy", "0.0123", "66050.1", why="Impacto por <reestructuración> & deuda",
                    impact=0.8, confidence=0.72, source="CoinDesk")
    assert t.priority is Priority.TRADE and "🟢 <b>Compré BTC</b>" in t.text and "sin plata real" in t.text
    assert "&lt;reestructuración&gt; &amp; deuda" in t.text and "66.050,1" in t.text and "(CoinDesk)" in t.text
    s = trade_alert("ETHUSDT", "sell", "1", "3000", why="hack")
    assert "🔴 <b>Vendí en corto ETH</b>" in s.text
    r = risk_alert("stop_loss", "BTCUSDT", "sell", "0.0123", "65390", pnl="-8.12")
    assert r.priority is Priority.RISK and "Corté la pérdida en BTC" in r.text and "-8,12 USDT" in r.text
    c = circuit_breaker_alert("pérdida del día 2.1% ≥ 2.0%: sin entradas hasta las 00:00 UTC")
    assert c.priority is Priority.RISK and "Freno del día" in c.text and "2,1 %" in c.text and "21:00" in c.text
    assert c.key == "circuit_breaker"
    for text in (t.text, s.text, r.text, c.text):                                 # nada de siglas ni jerga
        assert not any(w in text for w in ("LONG", "SHORT", "PnL", "STOP-LOSS", "CIRCUIT", "bps", "SL ", "TP "))
    long = trade_alert("X", "buy", "1", "1", why="x" * 10_000)
    assert len(long.text) <= MAX_TELEGRAM_CHARS


def test_redaccion_de_tokens(caplog, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert TOKEN not in redact(f"ClientResponseError: 502, url={url}")
    assert TOKEN not in redact(f"otro formato {TOKEN[:9]}:{TOKEN[10:]}")
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("x").error("fallo en %s", url)
    from tradingagent.telemetry.logs import RedactFilter
    rec = caplog.records[-1]
    RedactFilter().filter(rec)
    assert TOKEN not in rec.getMessage()


def test_el_redactor_de_muchos_textos_oculta_lo_mismo(monkeypatch):
    """`redactor` lee los secretos del entorno una vez (el tablero redacta cientos de líneas): mismo resultado."""
    from tradingagent.core.redact import redactor
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("UNA_API_KEY", "clave-secreta-de-prueba-123")
    clean = redactor(secrets=("otro-secreto-xyz",))
    for text in (f"url=https://api.telegram.org/bot{TOKEN}/x", "la clave-secreta-de-prueba-123 y otro-secreto-xyz",
                 "nada que ocultar", 12345):
        assert clean(text) == redact(text, secrets=("otro-secreto-xyz",))
    assert "clave-secreta" not in clean("ups: clave-secreta-de-prueba-123") and TOKEN not in clean(TOKEN)


def test_los_avisos_se_entienden_sin_saber_de_trading():
    from tradingagent.alerts.model import daily_alert, entry_alert, exit_alert
    e = entry_alert("BTC_USDT", 1, "0.0002", "100000", "20", stop="98500", tp="103000", p=0.63, category="hack",
                    title="Binance hacked: $40M in BTC stolen", source="coindesk", mode="PAPER futuros MEXC",
                    expiry_min=120)
    assert "🟢 <b>Compré BTC</b> · práctica en futuros MEXC (sin plata real)" in e.text
    assert "Puse 20,00 USDT con BTC a 100.000 USDT. Gano si sube." in e.text
    assert "Si baja a 98.500 vendo y pierdo ≈ 0,30 USDT; si sube a 103.000 vendo y gano ≈ 0,60 USDT." in e.text
    assert "Como mucho lo tengo 2 h." in e.text and "Chance estimada de ganar: 63 %." in e.text
    assert "un hackeo o robo — «Binance hacked: $40M in BTC stolen» (coindesk)" in e.text
    x = exit_alert("BTC_USDT", 1, "stop_loss", "0.0002", "98500", pnl="-0.31", ret="-0.0155", level="98500",
                   mode="PAPER futuros MEXC")
    assert "🔻 <b>Perdí -0,31 USDT con BTC</b> (-1,6 %)" in x.text and "límite de pérdida (98.500)" in x.text
    w = exit_alert("ETH_USDT", -1, "take_profit", "0.01", "3880", pnl="0.9", ret="0.03", level="3880")
    assert "✅ <b>Gané +0,90 USDT con ETH</b> (+3,0 %)" in w.text and "precio objetivo (3.880)" in w.text
    d = daily_alert("2026-11-02", mode="PAPER futuros MEXC", closed=0, won=0, pnl=0, equity="120", initial="120",
                    total_closed=3, llm={"calls": 0, "usd": 0.0, "failures": 2})
    assert "Resumen del 02/11" in d.text and "No hubo operaciones" in d.text and "Van 3 operaciones cerradas" in d.text
    assert "revisá la clave de Claude" in d.text and d.silent
    c = daily_alert("2026-11-05", mode="SIMULACRO futuros Binance", closed=2, won=1, pnl="0.8", equity="110.8",
                    initial="110", total_closed=12, calib={"acierto": 0.583, "cerradas": 12, "reaccion_s": 3.84})
    assert "Acierto: 58 % de 12 cerradas. De la noticia a la entrada: 3,8 s (mediana)." in c.text
    assert "Acierto" not in daily_alert("2026-11-05", mode="SIMULACRO futuros Binance", closed=0, won=0, pnl=0,
                                        equity="110", initial="110", total_closed=0,
                                        calib={"acierto": None, "cerradas": 0, "reaccion_s": None}).text
    for a in (e, x, w, d, c):
        assert not any(j in a.text for j in ("LONG", "SHORT", "PnL", "SL ", "TP ", "p=", "α", "bps", "DSR", "HALT"))


def test_el_resumen_diario_muestra_cada_estrategia():
    from tradingagent.alerts.model import daily_alert

    c = daily_alert("2026-11-06", mode="SIMULACRO futuros Binance", closed=1, won=1, pnl="1.2", equity="101.2",
                    initial="100", total_closed=4,
                    calib={"acierto": 0.75, "cerradas": 4, "reaccion_s": 2.1,
                           "por_categoria": {"deslistado_corto": {"n": 1, "ganadas": 1, "pnl": "0.9"},
                                             "resultados_impulso": {"n": 3, "ganadas": 2, "pnl": "1.5"}}})
    assert "Por estrategia: resultados 3 (2 ganadas, +1,50 USDT) · deslistados 1 (1 ganada, +0,90 USDT)." in c.text
