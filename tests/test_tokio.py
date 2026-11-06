"""Todo en Tokio (docs/RELE-TOKIO.md, fase 2): el relé reparte a la PC y al agente de allá, copia la pausa de la PC al
agente de Tokio, y la telemetría del agente de Tokio llega al tablero de la PC (resumen y alertas), sin montos si
algún día opera dinero real."""
import asyncio
import json
import secrets
import time

from tradingagent import dashboard, paths
from tradingagent.alerts.model import Alert, Priority
from tradingagent.core.clock import SystemClock
from tradingagent.core.events import NewsItem
from tradingagent.relay import client, protocol, server
from tradingagent.relay.tablero import TokioSink
from tradingagent.telemetry import remota

KEY = secrets.token_bytes(32)
PC = ("203.0.113.7", 40000)
LOCAL = ("127.0.0.1", 51000)


class Wire:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr=None):
        self.sent.append((data, addr))


def relay(tmp_path=None):
    r = server.RelayServer(KEY, kill_file=(tmp_path / "KILL") if tmp_path else None)
    r.transport = Wire()
    return r


def hb(r, addr, **kw):
    r.datagram_received(protocol.encode({"t": "hb", **kw}, KEY), addr)


def item(title="[거래] 신규 거래지원 안내 (FOO)"):
    now = time.time()
    return NewsItem(id="abc", source="upbit.avisos", tier=1, kind="telegram", title=title, published_at=now - 1,
                    received_at=now)


def test_el_rele_reparte_a_la_pc_y_al_agente_de_tokio():
    r = relay()
    hb(r, PC)
    hb(r, LOCAL)
    r.transport.sent.clear()
    assert r.forward(item())
    assert sorted(a for _, a in r.transport.sent) == sorted([PC, LOCAL])
    assert r.peer == PC                                              # la PC es la que está afuera


def test_la_telemetria_va_solo_del_agente_de_tokio_a_la_pc():
    r = relay()
    hb(r, PC)
    hb(r, LOCAL)
    r.transport.sent.clear()
    tel = protocol.encode({"t": "tel", "k": "agente", "up": 5}, KEY)
    r.datagram_received(tel, LOCAL)
    assert r.transport.sent == [(tel, PC)]                           # tal cual, con su firma
    r.transport.sent.clear()
    r.datagram_received(protocol.encode({"t": "tel", "k": "agente"}, KEY), PC)
    assert r.transport.sent == []                                    # de afuera no se reenvía nada


def test_la_pausa_de_la_pc_frena_a_tokio_y_solo_se_saca_la_propia(tmp_path):
    r = relay(tmp_path)
    kill = tmp_path / "KILL"
    hb(r, PC, kill=True)
    assert kill.read_text(encoding="utf-8").startswith(server.KILL_MARK)
    hb(r, PC, kill=False)
    assert not kill.exists()
    kill.write_text("pausa puesta a mano en el servidor", encoding="utf-8")
    hb(r, PC, kill=False)
    assert kill.exists()                                             # la que no puso el relé, queda
    kill.unlink()
    hb(r, LOCAL, kill=True)
    assert not kill.exists()                                         # solo la PC pausa a Tokio


async def test_el_cliente_avisa_la_pausa_apenas_cambia(tmp_path, monkeypatch):
    """Con UDP de verdad: el latido de la PC lleva el KILL y el relé lo copia en Tokio enseguida."""
    loop = asyncio.get_running_loop()
    clock = SystemClock()
    monkeypatch.setattr(server, "is_local", lambda addr: False)     # en la prueba todo es 127.0.0.1
    tokio_kill = tmp_path / "tokio-KILL"
    srv_t, srv = await loop.create_datagram_endpoint(lambda: server.RelayServer(KEY, clock, tokio_kill),
                                                     local_addr=("127.0.0.1", 0))
    pc_kill = tmp_path / "pc-KILL"

    async def nothing(*_a, **_k):
        return None

    pc = client.RelayClient("127.0.0.1", srv_t.get_extra_info("sockname")[1], KEY, nothing, clock,
                            heartbeat_s=5.0, kill_file=pc_kill)
    monkeypatch.setattr(client, "KILL_CHECK_S", 0.02)
    stop = asyncio.Event()
    task = asyncio.create_task(pc.run(stop))
    try:
        for _ in range(100):
            if srv.pc_kill is False:
                break
            await asyncio.sleep(0.02)
        assert srv.pc_kill is False and not tokio_kill.exists()
        pc_kill.write_text("pausa pedida por Telegram", encoding="utf-8")   # /pausa en la PC
        for _ in range(100):
            if tokio_kill.exists():
                break
            await asyncio.sleep(0.02)
        assert tokio_kill.exists() and srv.pc_kill is True             # sin esperar el latido de 5 s
    finally:
        stop.set()
        await task
        srv_t.close()


class FakeMarket:
    name = "binance-futuros"


class FakeTrader:
    def __init__(self, real, simulated, n_pos=0):
        self.real, self.simulated, self.market = real, simulated, FakeMarket()
        self.st = {"mercado": "binance-futuros", "initial": "100", "equity": "101.5", "cash": "90",
                   "stats": {"operaciones": 3, "pnl_neto": "1.2"}, "risk": {},
                   "positions": {f"COIN{i}USDT": {"side": "corto", "avg_price": "1.2345", "stop": "1.3",
                                                  "notional": "20.5", "entry_ts": 1_793_900_000.0,
                                                  "category": "deslistado_corto"} for i in range(n_pos)}}


class FakeAgent:
    def __init__(self, real_trader=None, trader=None):
        self.real_trader, self.trader, self.started = real_trader, trader, time.time() - 60

    def collect(self):
        return {"sources": {"ok": 20, "total": 21, "failing": ["upbit.avisos"] * 9},
                "news": {"aceptadas": 5, "duplicadas": 2, "stale": 1}, "loop_lag": {"p99_ms": 1.5},
                "peso_binance": {"usado": 40, "limite": 2400},
                "trader": {"llm": {"usd": 0.0012, "calls": 3, "failures": 0}}}


def test_el_resumen_entra_en_un_datagrama_y_trae_las_cuentas():
    t = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, True, n_pos=4), FakeTrader(False, False, n_pos=4)))
    msg = t.summary()
    assert len(protocol.encode(msg, KEY)) <= protocol.MAX_BYTES
    sim, practice = msg["cuentas"]
    assert (sim["tipo"], practice["tipo"]) == ("simulacro", "practica")
    assert sim["eq"] == 101.5 and sim["ret_pct"] == 1.5
    assert dict(zip(remota.POS_FIELDS, sim["pos"][0], strict=True))["px"] == 1.2345
    sent = []

    class C:
        def send(self, payload):
            sent.append(payload)
            return True

    t.attach(C())
    many = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, True, n_pos=40), FakeTrader(False, False, n_pos=40)))
    many.attach(C())
    assert many.send_summary()
    assert len(protocol.encode(sent[-1], KEY)) <= protocol.MAX_BYTES     # recorta posiciones hasta que entra


def test_con_dinero_real_la_telemetria_no_lleva_montos():
    t = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, False, n_pos=2)))
    acc = t.summary()["cuentas"][0]
    assert acc["tipo"] == "real" and acc["ret_pct"] == 1.5
    assert not {"eq", "ini", "pnl"} & set(acc)
    row = dict(zip(remota.POS_FIELDS, acc["pos"][0], strict=True))
    assert row["s"] == "COIN0USDT" and row["px"] is None and row["stop"] is None and row["nocional"] is None
    sent = []

    class C:
        def send(self, payload):
            sent.append(payload)
            return True

    t.attach(C())
    t._send_alert(Alert(Priority.TRADE, "trade", "<b>Entré</b> en BTC con 1234.56 USDT"))
    assert "1234" not in sent[-1]["texto"]


def test_cada_alerta_sigue_su_camino_y_ademas_va_a_la_pc():
    got, copies = [], []

    class Inner:
        def submit(self, alert):
            got.append(alert)
            return True

        def snapshot(self):
            return {"ok": True}

    tee = remota.AlertTee(Inner(), copies.append)
    a = Alert(Priority.TRADE, "trade", "<b>Entré</b> en FOO")
    assert tee.submit(a) and got == [a] and copies == [a] and tee.snapshot() == {"ok": True}
    t = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, True)))
    sent = []

    class C:
        def send(self, payload):
            sent.append(payload)
            return True

    t.attach(C())
    t._send_alert(a)
    assert sent[-1] == {"t": "tel", "k": "alerta", "tipo": "trade", "texto": "Entré en FOO"}


async def test_la_pc_guarda_lo_de_tokio_y_el_tablero_lo_muestra(tmp_path):
    home = paths.home()
    sink = TokioSink(home)
    t = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, True, n_pos=1)))
    await sink(protocol.decode(protocol.encode(t.summary(), KEY), KEY, protocol.NonceCache()))
    await sink({"t": "tel", "k": "rele", "pc": True, "agente": True,
                "telegram": {"conectado": True, "desde_s": 3600, "avisos": 2, "ignorados": 9,
                             "ultimo": {"titulo": "<script>x</script>", "demora_s": 0.9}}})
    await sink({"t": "tel", "k": "alerta", "tipo": "trade", "texto": "Entré en <b>FOO</b>"})
    saved = json.loads((home / "state" / "tokio.json").read_text(encoding="utf-8"))
    assert saved["agente"]["cuentas"][0]["tipo"] == "simulacro" and saved["rele"]["pc"] is True
    s = dashboard.gather(home)
    assert s["tokio"]["agente_hace_s"] <= 2 and s["tokio"]["eventos"][0]["tipo"] == "trade"
    html = dashboard.page(s)
    assert "Servidor de Tokio" in html and "el agente de allá está funcionando" in html
    assert "<script>x</script>" not in html and "&lt;b&gt;FOO" in html          # lo de afuera, escapado
    old = dashboard.gather(home, time.time() + 300)
    assert "sin noticias del agente" in dashboard.fragment(old)


async def test_lo_publicado_para_claude_no_lleva_dinero_real_de_tokio(tmp_path):
    home = paths.home()
    sink = TokioSink(home)
    t = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, False, n_pos=1)))
    await sink(t.summary())
    await sink({"t": "tel", "k": "alerta", "tipo": "trade", "texto": "(dinero real)"})
    pub = dashboard.gather(home, include_real=False)["tokio"]
    assert pub["cuentas"] == [] and pub["eventos"] == []
    assert dashboard.gather(home)["tokio"]["cuentas"][0]["tipo"] == "real"


def test_sin_nada_de_tokio_no_hay_seccion():
    assert dashboard.gather(paths.home())["tokio"] is None


async def test_de_punta_a_punta_el_agente_de_tokio_llega_al_tablero_de_la_pc(tmp_path, monkeypatch):
    """El agente de verdad en modo Tokio (relay.json local, con telemetría), el relé y la PC: el resumen y el aviso
    de arranque llegan al tablero de la PC, y un aviso del relé llega a la ingesta del agente de Tokio."""
    from test_journal_app import FreshBooks
    from tradingagent import config
    from tradingagent.app import Agent
    loop = asyncio.get_running_loop()
    clock = SystemClock()
    pc_port = []
    monkeypatch.setattr(server, "is_local", lambda addr: addr[1] not in pc_port)   # todo es 127.0.0.1 en la prueba
    srv_t, srv = await loop.create_datagram_endpoint(lambda: server.RelayServer(KEY, clock),
                                                     local_addr=("127.0.0.1", 0))
    port = srv_t.get_extra_info("sockname")[1]
    home = paths.home()                                            # el TRADING_HOME del agente de Tokio
    (home / "relay.key").write_text(KEY.hex(), encoding="utf-8")
    paths.write_json_atomic(home / "relay.json", {"host": "127.0.0.1", "port": port, "telemetria": True})
    pc_home = tmp_path / "pc"
    sink = TokioSink(pc_home)

    async def nothing(*_a, **_k):
        return None

    pc = client.RelayClient("127.0.0.1", port, KEY, nothing, clock, heartbeat_s=0.1, on_telemetry=sink)
    stop = asyncio.Event()
    pc_task = asyncio.create_task(pc.run(stop))
    cfg = config.load(None)
    cfg["trader"]["enabled"] = False                                # sin mercado real en este test
    cfg["alerts"]["telegram"] = False
    agent = Agent(cfg, {}, sources=[], book_provider=FreshBooks(), printer=lambda *_: None)
    run = asyncio.create_task(agent.run())
    try:
        for _ in range(300):
            if pc.transport is not None and not pc_port:
                pc_port.append(pc.transport.get_extra_info("sockname")[1])
            saved = paths.read_json(pc_home / "state" / "tokio.json", {})
            events = (pc_home / "state" / "tokio-eventos.jsonl")
            if saved.get("agente") and events.exists():
                break
            await asyncio.sleep(0.02)
        assert saved["agente"]["src"] == [0, 0] and saved["agente"]["kill"] is False
        assert "TRADING está funcionando" in events.read_text(encoding="utf-8")      # el aviso de arranque
        assert agent.telemetry.sent >= 2 and srv.counts["telemetria"] >= 2
        assert srv.forward(item())                                   # un aviso del relé: llega al agente de Tokio
        for _ in range(100):
            if agent.ingestion.stats()["counts"].get("aceptadas"):
                break
            await asyncio.sleep(0.02)
        assert agent.ingestion.stats()["counts"].get("aceptadas") == 1
    finally:
        agent.stop()
        await asyncio.wait_for(run, 20)
        stop.set()
        await pc_task
        srv_t.close()


async def test_solo_tablero_el_supervisor_recibe_a_tokio_y_le_pasa_la_pausa(tmp_path, monkeypatch):
    """Con la PC en "solo tablero", el monitor del supervisor (un hilo, solo biblioteca estándar) late al relé de
    verdad: recibe la telemetría de Tokio para el tablero y le pasa el KILL de la PC."""
    import socket as sk

    from tradingagent.relay.monitor import TokioMonitor
    loop = asyncio.get_running_loop()
    agent = sk.socket(sk.AF_INET, sk.SOCK_DGRAM)                    # el "agente de Tokio" (local para el relé)
    agent.bind(("127.0.0.1", 0))
    agent_port = agent.getsockname()[1]
    monkeypatch.setattr(server, "is_local", lambda addr: addr[1] == agent_port)
    tokio_kill = tmp_path / "tokio-KILL"
    srv_t, srv = await loop.create_datagram_endpoint(lambda: server.RelayServer(KEY, SystemClock(), tokio_kill),
                                                     local_addr=("127.0.0.1", 0))
    home = paths.home()
    mon = TokioMonitor(home, "127.0.0.1", srv_t.get_extra_info("sockname")[1], KEY, heartbeat_s=0.2, tick_s=0.05)
    mon.start()
    try:
        for _ in range(100):
            if srv.live(local=False) and mon.counts["acks"]:
                break
            await asyncio.sleep(0.02)
        assert mon.counts["acks"] and srv.pc_kill is False
        t = remota.RemoteTelemetry(FakeAgent(FakeTrader(True, True, n_pos=1)))
        agent.sendto(protocol.encode(t.summary(), KEY), srv_t.get_extra_info("sockname"))
        for _ in range(100):
            if (home / "state" / "tokio.json").exists():
                break
            await asyncio.sleep(0.02)
        assert paths.read_json(home / "state" / "tokio.json", {})["agente"]["cuentas"][0]["tipo"] == "simulacro"
        (home / "KILL").write_text("pausa pedida a mano en la PC", encoding="utf-8")
        for _ in range(100):
            if tokio_kill.exists():
                break
            await asyncio.sleep(0.02)
        assert tokio_kill.exists() and srv.pc_kill is True
    finally:
        mon.stop()
        agent.close()
        srv_t.close()
    assert not mon.alive()


async def test_el_tablero_en_solo_tablero_muestra_a_tokio_arriba(tmp_path):
    home = paths.home()
    await TokioSink(home)(remota.RemoteTelemetry(FakeAgent(FakeTrader(True, True))).summary())
    (home / "SOLO-TABLERO").write_text("", encoding="utf-8")
    s = dashboard.gather(home)
    assert s["solo_tablero"] and s["estado"] == "ok" and "corre en Tokio" in s["estado_texto"]
    html = dashboard.page(s)
    assert "La PC solo muestra el tablero" in html and "Salud del agente" not in html
    assert dashboard.gather(home, time.time() + 600)["estado"] == "caido"      # Tokio sin noticias


def test_el_tablero_de_tokio_se_ve_tal_cual_en_la_pc_y_lo_publico_nunca_lleva_dinero_real(tmp_path, monkeypatch):
    """Pedido del dueño: "en el dashboard no es necesaria esa distinción de PC y Tokio". El agente de Tokio exporta su
    tablero (local y público), la PC lo trae por SSH (relay/espejo.py) y lo muestra como propio. Con dinero real armado,
    la versión pública esconde la cuenta real (también en el latido: REGLAS G-8)."""
    import gzip
    import subprocess

    from tradingagent.relay import espejo
    tokio_home = tmp_path / "tokio"
    paths.write_json_atomic(tokio_home / "state" / "heartbeat.json",
                            {"ts": time.time(), "mode": "PAPER", "real": {"simulacro": False, "equity": "1234.5"},
                             "sources": {"ok": 13, "total": 13, "failing": []}})
    board = dashboard.export_board(tokio_home)
    assert board["local"]["agente"]["real"]["equity"] == "1234.5"
    assert "1234.5" not in json.dumps(board["publico"])                         # lo público, sin el saldo real
    exported = (tokio_home / "state" / dashboard.EXPORT_FILE).read_bytes()

    def fake_run(cmd, **kw):                                                    # el "ssh" a Tokio
        assert cmd[-1].startswith("gzip -c ") and "BatchMode=yes" in cmd
        return subprocess.CompletedProcess(cmd, 0, gzip.compress(exported), b"")

    monkeypatch.setattr(espejo.subprocess, "run", fake_run)
    pc = paths.home()
    m = espejo.TokioBoardMirror(pc, "203.0.113.9", key=tmp_path / "k.pem", ssh="ssh")
    assert m.fetch_once() and m.counts["ok"] == 1
    assert dashboard.gather(pc).get("espejo") is None                          # sin SOLO-TABLERO, el de la PC
    (pc / "SOLO-TABLERO").write_text("", encoding="utf-8")
    s = dashboard.gather(pc)
    assert s["espejo"] and s["solo_tablero"] and s["agente"]["real"]["equity"] == "1234.5"
    assert "Servidor de Tokio" not in dashboard.page(s)                         # todo es uno
    pub = dashboard.gather(pc, include_real=False)                             # lo que se publica en la rama pc
    assert "1234.5" not in json.dumps(pub, default=str)
    assert dashboard.gather(pc, time.time() + 600).get("espejo") is None       # viejo: no se usa


def test_el_latido_publicado_por_el_tablero_esconde_la_cuenta_real():
    home = paths.home()
    paths.write_json_atomic(home / "state" / "heartbeat.json",
                            {"ts": time.time(), "real": {"simulacro": False, "equity": "999.9"}})
    assert "999.9" not in json.dumps(dashboard.gather(home, include_real=False), default=str)
    assert dashboard.gather(home)["agente"]["real"]["equity"] == "999.9"        # en la PC, sí
