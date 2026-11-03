"""Supervisor de la PC (supervisor.py): se actualiza solo con CI verde y vuelve atrás si la versión nueva no
arranca, relanza al agente, nunca corren dos agentes, la parada con calma y la publicación para Claude sin
secretos. Contra un origin git local, una API de GitHub falsa y un Telegram falso (servidores reales)."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import serve
from fake_telegram import TOKEN, FakeTelegram
from tradingagent import config, paths, supervisor
from tradingagent.__main__ import main as cli
from tradingagent.app import Agent
from tradingagent.core.instance import InstanceLock
from tradingagent.supervisor import Supervisor

CHILD = r"""
import os, pathlib, sys, time
home = pathlib.Path(sys.argv[1])
(home / "state").mkdir(parents=True, exist_ok=True)
with open(home / "hijos.txt", "a") as f:
    f.write(f"{os.getpid()} {os.environ.get('PYTHONUTF8')}\n")
if sys.argv[2] != "vive":
    sys.exit(int(sys.argv[2]))
while not (home / "state" / "parar").exists():
    time.sleep(0.05)
"""
ORPHAN = r"""
import pathlib, sys, time
from tradingagent.core.instance import InstanceLock
home = pathlib.Path(sys.argv[1])
lock = InstanceLock(home / "state" / "agente.lock")
assert lock.acquire()
(home / "huerfano-listo").write_text("si")
while not (home / "state" / "parar").exists():
    time.sleep(0.05)
"""
CI_SIN_PC = "on:\n  push:\n    branches-ignore: [data]\n"
CI_CON_PC = "on:\n  push:\n    branches-ignore: [data, pc]\n"


def sh(*args, cwd=None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


class Origin:
    """Un "GitHub" local: origin desnudo, una copia para hacer commits y la copia de la PC."""

    def __init__(self, tmp):
        self.bare, self.dev, self.pc = tmp / "origin.git", tmp / "dev", tmp / "pc"
        sh("init", "-q", "--bare", "-b", "main", str(self.bare))
        sh("init", "-q", "-b", "main", str(self.dev))
        for k, v in (("user.name", "dev"), ("user.email", "dev@example.com"), ("commit.gpgsign", "false")):
            sh("config", k, v, cwd=self.dev)
        self.commit({"README.md": "v1\n", ".github/workflows/ci.yml": CI_SIN_PC,
                     "src/tradingagent/supervisor.py": "# v1\n", "pyproject.toml": "v1\n"}, "primera")
        sh("clone", "-q", str(self.bare), str(self.pc))

    def commit(self, files: dict, msg: str, remove=()) -> str:
        for name, text in files.items():
            path = self.dev / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for name in remove:
            sh("rm", "-q", name, cwd=self.dev)
        sh("add", "-A", cwd=self.dev)
        sh("commit", "-q", "-m", msg, cwd=self.dev)
        sh("push", "-q", str(self.bare), "HEAD:refs/heads/main", cwd=self.dev)
        return sh("rev-parse", "HEAD", cwd=self.dev)


class FakeGitHub:
    """GET /actions/runs?head_sha=... con las corridas que el test programa por commit."""

    def __init__(self):
        self.runs: dict[str, list[dict]] = {}
        self.calls = 0
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                owner.calls += 1
                q = parse_qs(urlparse(self.path).query)
                body = json.dumps({"workflow_runs": owner.runs.get(q["head_sha"][0], [])}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def set(self, sha, status="completed", conclusion="success", path=supervisor.CI_WORKFLOW, event="push"):
        self.runs[sha] = [{"path": path, "event": event, "head_sha": sha, "status": status, "conclusion": conclusion}]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Sup(Supervisor):
    """El supervisor real, sin pip ni pytest de verdad: la prueba de arranque falla si existe el archivo ROTO."""

    def __init__(self, *a, **kw):
        self.smoked, self.installs, self.tests_runs = [], 0, 0
        super().__init__(*a, **kw)

    def smoke(self):
        self.smoked.append(self.head()[:7])
        broken = (self.repo / "ROTO").exists()
        return (False, "ImportError: falta un módulo") if broken else (True, "")

    def install(self):
        self.installs += 1
        return True

    def run_tests(self):
        self.tests_runs += 1
        self.state["tests_version"] = self.version[:7]

    def loaded_files(self):
        return {"src/tradingagent/supervisor.py"}


def make(tmp_path, gh, origin, env=None, mode="vive", port=None):
    home = tmp_path / "home"
    return Sup(repo=origin.pc, home=home, api=gh.url, remote=str(origin.bare), env=env or {}, port=port,
               child_cmd=[sys.executable, "-c", CHILD, str(home), mode])


def wait_exit(sup, timeout=10):
    t0 = time.monotonic()
    while sup.child is not None and sup.child.poll() is None and time.monotonic() - t0 < timeout:
        time.sleep(0.02)


@pytest.fixture
def gh():
    g = FakeGitHub()
    yield g
    g.close()


async def test_se_actualiza_solo_con_ci_verde_espera_posiciones_y_vuelve_atras(tmp_path, gh):
    origin, tg = Origin(tmp_path), FakeTelegram()
    async with serve(tg.app()) as turl:
        env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "4242", "TELEGRAM_API_BASE": turl}
        sup = make(tmp_path, gh, origin, env)
        run = asyncio.to_thread
        await run(sup.start_child)
        assert await run(sup.check_update) == "al_dia"
        b = origin.commit({"README.md": "v2\n"}, "mejora del clasificador")
        assert await run(sup.check_update) == "esperando_ci:sin_ci"            # sin CI no se toca nada
        gh.set(b, path=".github/workflows/recoleccion.yml")                     # otro workflow no cuenta
        assert await run(sup.check_update) == "esperando_ci:sin_ci"
        gh.set(b, event="pull_request")
        assert await run(sup.check_update) == "esperando_ci:sin_ci"
        gh.set(b, status="in_progress", conclusion=None)
        assert await run(sup.check_update) == "esperando_ci:en_curso"
        gh.set(b, conclusion="failure")
        assert await run(sup.check_update) == "esperando_ci:rojo"
        gh.set(b)
        acct = sup.home / "paper-vivo" / "mexc-futuros" / "estado.json"
        paths.write_json_atomic(acct, {"positions": {"BTC_USDT": {"side": "largo"}}})
        assert await run(sup.check_update) == "esperando_cierre:1"              # no corta una posición abierta
        assert sup.child.poll() is None and sup.head() != b
        paths.write_json_atomic(acct, {"positions": {}})
        assert await run(sup.check_update) == "actualizado"
        assert sup.head() == b and sup.version == b and sup.child is None      # el agente paró con calma
        assert (sup.home / "state" / "parar").exists() and sup.installs == 0   # sin dependencias nuevas: sin pip
        await run(sup.start_child)
        assert not (sup.home / "state" / "parar").exists()                      # el pedido viejo no lo frena

        c = origin.commit({"ROTO": "x\n"}, "versión rota")
        gh.set(c)
        assert await run(sup.check_update) == "rechazada"
        assert sup.head() == b and sup.state["rechazada"] == c                  # volvió a la que andaba
        await run(sup.start_child)
        assert await run(sup.check_update) == "rechazada_antes"                 # no la reintenta

        d = origin.commit({"src/tradingagent/supervisor.py": "# v2\n", "pyproject.toml": "v2\n"},
                          "supervisor nuevo", remove=("ROTO",))
        gh.set(d)
        assert await run(sup.check_update) == "recargar_supervisor"            # se relanza con el código nuevo
        assert sup.head() == d and sup.installs == 1                           # cambió pyproject: reinstala
        await run(sup.stop_child)
    texts = [m["text"] for m in tg.messages]
    assert any(t.startswith("✅ TRADING actualizado a " + b[:7]) for t in texts)
    assert any("no arrancó en tu PC" in t and c[:7] in t for t in texts)
    rows = paths.read_json(sup.state_file, {})["actualizaciones"]
    assert [r["resultado"] for r in rows] == ["ok", "rechazada", "ok"] and "falta un módulo" in rows[1]["error"]


async def test_relanza_al_agente_y_avisa_una_vez_si_se_cae_seguido(tmp_path, gh):
    origin, tg = Origin(tmp_path), FakeTelegram()
    async with serve(tg.app()) as turl:
        env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "4242", "TELEGRAM_API_BASE": turl}
        sup = make(tmp_path, gh, origin, env, mode="1")
        waits = []
        for _ in range(4):
            await asyncio.to_thread(sup.start_child)
            await asyncio.to_thread(wait_exit, sup)
            waits.append(await asyncio.to_thread(sup.watch_child))
        sup.child_cmd[-1] = str(supervisor.EXIT_LOCKED)
        await asyncio.to_thread(sup.start_child)
        await asyncio.to_thread(wait_exit, sup)
        await asyncio.to_thread(sup.watch_child)
    assert waits == [2.0, 5.0, 15.0, 30.0]                                      # espera creciente
    lines = (sup.home / "hijos.txt").read_text().split("\n")[:-1]
    assert len(lines) == 5 and all(x.endswith(" 1") for x in lines)            # el agente corre en modo UTF-8
    assert sum("se cayó" in m["text"] for m in tg.messages) == 1                 # un aviso por hora, no spam
    crashes = sup.state["caidas"]
    assert crashes[0]["codigo"] == 1 and crashes[-1]["motivo"] == "otro_agente_corriendo"
    assert sup.state["arranques"] == 5


def test_nunca_dos_agentes_y_el_huerfano_se_para_con_calma(tmp_path, gh, capsys):
    a, b = InstanceLock(tmp_path / "x.lock"), InstanceLock(tmp_path / "x.lock")
    assert a.acquire() and not b.acquire() and b.held_by_other()
    a.release()
    assert b.acquire() and a.held_by_other()
    b.release()

    home = paths.home()
    held = InstanceLock(home / "state" / "agente.lock")
    assert held.acquire()
    assert cli(["run", "--seconds", "1"]) == supervisor.EXIT_LOCKED              # ya hay uno: no arranca otro
    assert "Ya hay un agente corriendo" in capsys.readouterr().out
    held.release()

    origin = Origin(tmp_path)
    sup = make(tmp_path, gh, origin)
    assert sup.adopt_orphan() == "ninguno"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    orphan = subprocess.Popen([sys.executable, "-c", ORPHAN, str(sup.home)], env=env)
    try:
        for _ in range(400):
            if (sup.home / "huerfano-listo").exists():
                break
            time.sleep(0.02)
        assert sup.adopt_orphan(timeout_s=10) == "parado"
        assert orphan.wait(timeout=5) == 0
    finally:
        if orphan.poll() is None:
            orphan.kill()


async def test_el_agente_cierra_en_orden_con_el_archivo_parar():
    from test_journal_app import FreshBooks

    stop = paths.home() / "state" / "parar"
    stop.write_text("viejo")                                                    # pedido de antes de arrancar
    cfg = config.load(None)
    cfg["trader"]["enabled"] = False
    agent = Agent(cfg, {}, sources=[], book_provider=FreshBooks(), printer=lambda *_: None)
    run = asyncio.create_task(agent.run())
    await asyncio.sleep(1.5)
    assert not run.done() and not stop.exists()                                 # el viejo no lo frena
    t0 = time.monotonic()
    stop.write_text("actualizacion")
    await asyncio.wait_for(run, timeout=10)
    assert time.monotonic() - t0 < 5


def test_publica_para_claude_sin_secretos_un_solo_commit(tmp_path, gh, monkeypatch):
    secret = "secreto-de-prueba-123456789"
    monkeypatch.setenv("MEXC_SECRET_KEY", secret)
    origin = Origin(tmp_path)
    sup = make(tmp_path, gh, origin, env={"GITHUB_TOKEN_PC": "gho_tokenfalso0123456789"})
    home = sup.home
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text(f"MEXC_SECRET_KEY={secret}\n")
    paths.write_json_atomic(home / "state" / "heartbeat.json", {"ts": time.time(), "mode": "PAPER",
                                                                "log": str(home / "logs")})
    base = home / "paper-vivo" / "mexc-futuros"
    paths.write_json_atomic(base / "estado.json", {"initial": "120", "cash": "120", "positions": {},
                                                   "stats": {"operaciones": 0}})
    (base / "decisiones").mkdir(parents=True)
    (base / "decisiones" / "2026-11.jsonl").write_text(json.dumps({"title": "Noticia", "decision": "no_entrar"}) + "\n")
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "logs" / "agent.log").write_text(
        json.dumps({"ts": 1, "level": "WARNING", "logger": "x", "msg": f"falló con {secret} en {home}"},
                   ensure_ascii=False) + "\n"
        + json.dumps({"ts": 2, "level": "INFO", "logger": "x", "msg": "todo bien"}) + "\n", encoding="utf-8")
    assert sup.publish() == "esperando_ci"                                      # el CI todavía no ignora `pc`
    assert Supervisor.publish(make(tmp_path, gh, origin)) == "sin_token"
    (origin.pc / ".github" / "workflows" / "ci.yml").write_text(CI_CON_PC)
    assert sup.publish() == "ok" and sup.publish() == "ok"
    assert sh("rev-list", "--count", "pc", cwd=origin.bare) == "1"              # la rama no crece
    files = sh("ls-tree", "-r", "--name-only", "pc", cwd=origin.bare).splitlines()
    assert {"tablero.json", "heartbeat.json", "errores.jsonl", "LEEME.md", "paper-vivo/mexc-futuros/estado.json",
            "paper-vivo/mexc-futuros/decisiones-recientes.jsonl"} <= set(files)
    assert not any(f.endswith(".env") or "journal" in f for f in files)
    for f in files:
        text = sh("show", f"pc:{f}", cwd=origin.bare)
        assert secret not in text and str(home) not in text and "gho_tokenfalso" not in text, f
    errors = sh("show", "pc:errores.jsonl", cwd=origin.bare)
    assert "falló con «oculto» en ~" in errors and "todo bien" not in errors


def test_los_tests_de_la_pc_corren_sin_claves_y_con_carpeta_aparte(tmp_path, gh):
    origin = Origin(tmp_path)
    dump = tmp_path / "entorno.json"
    code = tmp_path / "espia.py"
    code.write_text(f"import json, os, sys\njson.dump({{'argv': sys.argv[1:], 'env': dict(os.environ)}}, "
                    f"open({str(dump)!r}, 'w'))\n")
    if os.name == "nt":                                                       # un "python" falso que anota todo
        spy = tmp_path / "fakepy.cmd"
        spy.write_text(f'@"{sys.executable}" "{code}" %*\n')
    else:
        spy = tmp_path / "fakepy"
        spy.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{code}' \"$@\"\n")
        spy.chmod(0o755)
    env = {**os.environ, "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "4242", "MEXC_SECRET_KEY": "x" * 20,
           "GITHUB_TOKEN_PC": "gho_x", "ANTHROPIC_API_KEY": "sk-ant-x", "TRADING_HOME": "C:/real",
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraheader", "GIT_CONFIG_VALUE_0": "AUTH: x"}
    sup = Supervisor(repo=origin.pc, home=tmp_path / "home", python=str(spy), env=env, port=None)
    sup.run_tests()
    assert sup.tests.wait(timeout=20) == 0
    sup.tests_done()
    got = json.loads(dump.read_text())
    assert got["argv"][:2] == ["-m", "pytest"]
    assert got["env"]["TRADING_HOME"] == str(tmp_path / "home" / "pruebas")
    assert not {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "MEXC_SECRET_KEY", "GITHUB_TOKEN_PC",
                "ANTHROPIC_API_KEY"} & set(got["env"])
    assert not [k for k in got["env"] if k.startswith("GIT_CONFIG")]          # la familia entera, no a medias
    assert got["env"]["PYTHONUTF8"] == "1" and got["env"]["PYTHONIOENCODING"] == "utf-8"
    assert sup.state["tests_resultado"]["codigo"] == 0 and sup.tests is None


def test_el_bucle_arranca_agente_tablero_tests_y_revisa(tmp_path, gh, monkeypatch):
    import urllib.request

    monkeypatch.setattr(supervisor, "FIRST_CHECK_S", 0.0)
    monkeypatch.setattr(supervisor, "FIRST_PUBLISH_S", 0.0)
    origin = Origin(tmp_path)
    sup = make(tmp_path, gh, origin, port=0)
    seen = {}
    real_check = sup.check_update

    def check():
        seen["tablero"] = urllib.request.urlopen(supervisor.dashboard.url(sup.board) + "/api/estado",
                                                 timeout=5).status
        seen["agente_vivo"] = sup.child is not None and sup.child.poll() is None
        return real_check()

    sup.check_update = check
    assert sup.run(max_loops=2, tick_s=0.01) == 0
    assert seen == {"tablero": 200, "agente_vivo": True}
    assert sup.tests_runs == 1 and sup.child is None                            # al salir, el agente cerró
    st = paths.read_json(sup.state_file, {})
    assert st["ultima_revision"]["resultado"] == "al_dia" and st["publicacion"]["resultado"] == "sin_token"


def test_borra_aunque_haya_solo_lectura_y_guardar_nunca_lo_tumba(tmp_path, gh, monkeypatch):
    import stat

    d = tmp_path / "publicar" / ".git" / "objects"
    d.mkdir(parents=True)
    f = d / "ab12"
    f.write_text("x")
    f.chmod(stat.S_IREAD)                                                    # como los deja git en Windows
    supervisor.rmtree(tmp_path / "publicar")
    assert not (tmp_path / "publicar").exists()
    supervisor.rmtree(tmp_path / "no-existe")

    origin = Origin(tmp_path)
    sup = make(tmp_path, gh, origin)
    real, fails = paths.write_json_atomic, []

    def flaky(path, data):
        if len(fails) < 2:
            fails.append(1)
            raise PermissionError("en uso")
        real(path, data)

    monkeypatch.setattr(supervisor.paths, "write_json_atomic", flaky)
    sup.state["x"] = 1
    sup.save()                                                               # reintenta y guarda
    assert paths.read_json(sup.state_file, {})["x"] == 1
    monkeypatch.setattr(supervisor.paths, "write_json_atomic", lambda *_a: (_ for _ in ()).throw(OSError("lleno")))
    sup.save()                                                               # no lanza: sigue andando


async def test_la_consola_de_windows_nunca_frena_las_noticias_ni_el_latido(tmp_path, monkeypatch):
    """Con la salida en un archivo, Windows escribe en cp1252: un emoji no puede frenar al operador."""
    import io

    from test_live import news
    from tradingagent import __main__ as main_mod
    from tradingagent.core.bus import EventBus
    from tradingagent.ingestion.engine import TOPIC_ACCEPTED
    from tradingagent.telemetry.heartbeat import Heartbeat

    def cp1252_console(text):
        text.encode("cp1252")                                                   # lanza con 📰 y con ♥

    got = []

    class Trader:
        async def on_news(self, ev):
            got.append(ev.item.id)

    agent = Agent(config.load(None), {}, printer=cp1252_console)
    agent.bus, agent.trader = EventBus(), Trader()
    task = asyncio.create_task(agent._print_accepted())
    await asyncio.sleep(0)
    for i in range(2):
        agent.bus.publish(TOPIC_ACCEPTED, news(f"n{i}", "Bitcoin ETF approved", time.time()))
    await asyncio.sleep(0.05)
    task.cancel()
    assert got == ["n0", "n1"]                                                  # el operador las recibió igual
    hb = Heartbeat(lambda: {"mode": "PAPER"}, state_file=tmp_path / "hb.json", printer=cp1252_console)
    await hb.beat()
    assert json.loads((tmp_path / "hb.json").read_text(encoding="utf-8"))["mode"] == "PAPER"

    raw, err = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err, encoding="cp1252"))
    main_mod._utf8_console()                                                    # lo que hace cada comando
    print("📰 ♥ ñ")
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8").strip() == "📰 ♥ ñ"


async def test_al_parar_con_calma_la_noticia_en_curso_se_termina_de_operar():
    from test_journal_app import FreshBooks
    from test_live import news
    from tradingagent.ingestion.engine import TOPIC_ACCEPTED

    done = []

    class SlowTrader:
        llm = None

        async def on_news(self, ev):
            await asyncio.sleep(0.5)                                            # p. ej., esperando a Claude
            done.append(ev.item.id)

        async def watch(self, stop):
            await stop.wait()

        def snapshot(self):
            return {}

    cfg = config.load(None)
    cfg["trader"]["enabled"] = False
    agent = Agent(cfg, {}, sources=[], book_provider=FreshBooks(), printer=lambda *_: None)
    agent.trader = SlowTrader()
    run = asyncio.create_task(agent.run())
    for _ in range(300):
        if getattr(agent, "execution", None):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    agent.bus.publish(TOPIC_ACCEPTED, news("n1", "Bitcoin ETF approved", time.time()))
    await asyncio.sleep(0.05)
    (paths.home() / "state" / "parar").write_text("actualizacion")              # el supervisor pide parar
    await asyncio.wait_for(run, timeout=15)
    assert done == ["n1"]
