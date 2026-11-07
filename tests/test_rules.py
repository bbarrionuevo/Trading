"""Las reglas no son de papel: cada regla de docs/REGLAS.md que se puede comprobar en el código tiene su
test acá (ruta protegida, REGLAS G-2). Si uno falla, el CI queda en rojo."""
import copy
import re
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

from tradingagent import config, limits, paths, safety
from tradingagent.execution.orders import OrderIntent, Side

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tradingagent"


def repo_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.splitlines()
        return [ROOT / p for p in out if (ROOT / p).is_file()]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def code_files() -> list[Path]:
    return [p for p in repo_files() if p.suffix == ".py" and ("src" in p.parts or "tools" in p.parts)]


# -- A. Sobre de riesgo -------------------------------------------------------------------------
def test_a1_el_sobre_del_repo_respeta_la_linea_base():
    assert limits.problems(limits.load_raw()) == []
    assert limits.REPO_FILE.exists()


@pytest.mark.parametrize("key", sorted(limits.BASELINE))
def test_a1_relajar_cualquier_limite_se_rechaza(key, tmp_path):
    lim = copy.deepcopy(limits.load_raw())
    kind, base = limits.BASELINE[key]
    sec, name = key
    lim[sec][name] = {"max": base * 2 if base else 1, "min": base / 2, "eq": not base}[kind]
    assert any(f"{sec}.{name}" in p for p in limits.problems(lim)), key
    f = tmp_path / "limits.yaml"
    f.write_text(yaml.safe_dump(lim))
    with pytest.raises(ValueError):                    # el agente tampoco arranca con ese sobre
        limits.load(f)


def test_a1_ajustar_a_la_baja_siempre_se_permite():
    lim = copy.deepcopy(limits.load_raw())
    lim["loss"]["daily_pct"] = 1.0
    lim["per_trade"]["max_slippage_bps"] = 20
    lim["loss"]["cooldown_min"] = 120
    assert limits.problems(lim) == []


def test_a2_dinero_real_solo_con_el_armado_del_dueno(tmp_path):
    assert config.DEFAULTS["mode"] == "paper"                     # la configuración nunca pasa a real sola
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["mode"] = "live"
    assert any("solo opera en paper" in e for e in config.validate(cfg))
    from tradingagent.execution.live import arming
    lim = limits.load()
    home = tmp_path / "h"
    assert arming.MAX_DAYS == 30 and lim["capital"]["todo_el_saldo"] is True      # sin tope de capital (dueño)
    for days, typed, sim, capital in ((7, "si", False, None), (31, None, False, None), (7, None, True, 0),
                                      (31, None, True, 100)):
        with pytest.raises(ValueError):                          # frase exacta, A-2 (30 días), simulacro con saldo
            arming.arm(home, days, "k", typed or arming.phrase(days), simulacro=sim, capital=capital)
    assert arming.status(home, "k")[1] == "no_armado"
    callers = sorted(f.name for f in code_files() if "arming.arm(" in f.read_text(encoding="utf-8"))
    assert callers == ["__main__.py"]                             # solo el comando del dueño arma


def test_g9_un_solo_ejecutor_del_dinero_real(tmp_path):
    """Si el agente de Tokio informa (telemetría de la última hora) que opera dinero real, la PC no abre con dinero
    real; el simulacro sigue. El relé copia la pausa de la PC a Tokio y nunca lee claves de trading."""
    import time

    from tradingagent.execution.live import arming
    from tradingagent.relay import server, telegram_upbit
    home, now = tmp_path / "h", time.time()
    arming.arm(home, 7, "k", arming.phrase(7), now=now)
    arming.save_verification(home, {"ok": True}, "k", now=now)
    assert arming.status(home, "k", now=now + 60)[0]

    def tokio(kind, ago):
        paths.write_json_atomic(home / "state" / "tokio.json",
                                {"agente": {"recibido": now - ago, "cuentas": [{"tipo": kind}]}})

    tokio("real", 30)
    assert arming.status(home, "k", now=now + 60)[1] == "otro_ejecutor"
    tokio("simulacro", 30)
    assert arming.status(home, "k", now=now + 60)[0]
    tokio("real", 2 * 3600)                                      # Tokio calló hace 2 h: decide el armado de acá
    assert arming.status(home, "k", now=now + 60)[0]
    assert server.KILL_MARK and telegram_upbit.WANTED == ("TG_API_ID", "TG_API_HASH")
    unit = (ROOT / "deploy" / "rele-tokio" / "rele-tokio.service").read_text(encoding="utf-8")
    assert "EnvironmentFile" not in [line.split("=")[0].strip() for line in unit.splitlines()]


def test_l_lineamientos_medidos_trabados_en_el_codigo():
    """REGLAS L-1, L-3, L-7, L-9, L-10 y L-11: lo aprendido con datos no se cambia por error."""
    from tradingagent.decision import binance_listings, korean_notices, policy, upbit_listings
    from tradingagent.decision import delistings as delistings_mod
    from tradingagent.research import shadow
    assert upbit_listings.MAX_LATE_S <= 3.0                                  # L-1: al instante, por Telegram
    assert binance_listings.MAX_LATE_S <= 2.5 and binance_listings.MAX_RISE <= 0.08   # L-10: a los 3,5 s ya es tarde
    live_src = (SRC / "live.py").read_text(encoding="utf-8")
    start = live_src.index("    async def on_news(")
    on_news = live_src[start:live_src.index("\n    async def ", start + 10)]
    assert on_news.index("self._upbit_listing(") < on_news.index("self.llm.classify(")   # primero entra, después la IA
    assert on_news.index("self._binance_listing(") < on_news.index("self.llm.classify(")
    assert on_news.index("self._korean(") < on_news.index("self.llm.classify(")
    lim = limits.load()                                                         # L-3: 3 % y sin apalancamiento
    assert float(lim["per_trade"]["max_risk_pct"]) <= 3 and int(lim["per_trade"]["max_leverage"]) == 1
    for name in ("listado_upbit", "listado_binance"):
        p = policy.EXITS[name]
        assert p.stop == 0.10 and p.max_x * p.stop <= 0.03 + 1e-9
    for name, risk in (("deslistado_corto", 0.02), ("advertencia_upbit", 0.02), ("fin_soporte_corto", 0.01)):
        p = policy.EXITS[name]                                                  # L-3: muestras chicas, menos
        assert p.max_x * p.stop <= risk + 1e-9, name
    # L-9: avisos coreanos. La advertencia baja de a poco (hasta 30 s, si no cayó 3 %); el fin del soporte cae en
    # segundos (hasta 5 s, si no cayó 6 %). La advertencia de Bithumb no se opera.
    adv, fin = korean_notices.BY_CATEGORY["advertencia_upbit"], korean_notices.BY_CATEGORY["fin_soporte_corto"]
    assert adv.max_late_s <= 30 and adv.max_drop <= 0.03 and not adv.jump and "bithumb.avisos" not in adv.sources
    assert fin.max_late_s <= 5 and fin.max_drop <= 0.06 and fin.jump
    # L-11: el tope de la orden, por estrategia y medido. 2 % solo en el deslistado de Binance; lo demás ≤ 0,5 %; una
    # orden sin tope pedido, 0,5 % (el del sobre es solo el techo).
    for name, p in policy.EXITS.items():                   # 2 %: deslistado y vigilancia de Binance (7-nov)
        assert p.max_slippage_bps <= (200 if name in ("deslistado_corto", "vigilancia_corto") else 50), name
    assert delistings_mod.BY_CATEGORY["vigilancia_corto"].max_drop <= 0.04
    assert policy.PolicyConfig().max_slippage_bps <= 50 and shadow.EXIT_SLIPPAGE_BPS <= 50
    assert safety.DEFAULT_SLIPPAGE_BPS == 50 and int(lim["per_trade"]["max_slippage_bps"]) <= 200
    venue = (SRC / "execution" / "live" / "binance_venue.py").read_text(encoding="utf-8")   # L-7: nunca antes de 5 s
    assert float(re.search(r"^MIN_CANCEL_AGE_S = ([\d.]+)", venue, re.M).group(1)) >= 5


def test_a3_ordenes_solo_en_el_modulo_real_y_nunca_retiros_ni_transferencias():
    everywhere = [r"capital/withdraw", r"\bwithdraw(al)?s?\s*\(", r"/withdraw", r"/transfer", r"X-MEXC-APIKEY",
                  r"\bccxt\b", r"/api/v3/order\b", r"\bnewOrder\b", r"\bplaceOrder\b", r"change_margin",
                  r"change_position_mode", r"/private/(plan|stop)order", r"/fapi/v\d/(batchOrders|listenKey)"]
    only_live = [r"/private/order", r"change_leverage", r"/private/position/(?!open_positions)"]
    only_binance = [r"X-MBX-APIKEY", r"/fapi/v\d/order", r"/fapi/v\d/(leverage|marginType|allOpenOrders|algo|openAlgo)"]
    live = SRC / "execution" / "live" / "mexc_futures.py"
    binance = SRC / "execution" / "live" / "binance_futures.py"
    news = SRC / "ingestion" / "binance_ws.py"          # anuncios por WebSocket, con clave de SOLO LECTURA (4-nov)
    hits = []
    for f in code_files():
        text = f.read_text(encoding="utf-8")
        hits += [f"{f.relative_to(ROOT)}: {b}" for b in everywhere if re.search(b, text)]
        if f != live:
            hits += [f"{f.relative_to(ROOT)}: {b}" for b in only_live if re.search(b, text)]
        if f not in (binance, news):                     # órdenes de Binance: solo en su cliente (4-nov, el dueño)
            hits += [f"{f.relative_to(ROOT)}: {b}" for b in only_binance if re.search(b, text)]
    text = news.read_text(encoding="utf-8")              # el canal de anuncios firma, pero no opera:
    hits += [f"{news.relative_to(ROOT)}: {b}" for b in (r"/fapi/", r"/sapi/v\d", r"BINANCE_API_(KEY|SECRET)",
                                                         r"\border", r"/api/v3/(?!time)") if re.search(b, text)]
    assert set(re.findall(r"wss://[^\s\"'`)]+", text)) == {"wss://api.binance.com/sapi/wss"}   # solo los anuncios
    assert "BINANCE_LECTURA_KEY" in text
    dry = (SRC / "execution" / "live" / "binance_dryrun.py").read_text(encoding="utf-8")   # el simulacro local:
    assert not re.search(r"BINANCE_API_(KEY|SECRET)", dry)                # nunca la clave de trading
    assert set(re.findall(r"self\.reader\.(\w+)", dry)) <= {"request", "usdt"}  # de la de lectura, solo leer
    assert set(re.findall(r"self\.reader\.request\(\"(\w+)\"", dry)) == {"GET"}
    assert hits == []
    from tradingagent.execution.live import binance_futures as bf
    assert bf.GET_PATHS == ("/fapi/v2/balance", "/fapi/v2/positionRisk", "/fapi/v1/positionSide/dual",
                            "/fapi/v1/multiAssetsMargin", "/fapi/v1/order", "/fapi/v1/openOrders", "/fapi/v1/userTrades",
                            "/fapi/v1/apiTradingStatus", "/fapi/v1/commissionRate", "/fapi/v1/algoOrder",
                            "/fapi/v1/openAlgoOrders")
    assert bf.POST_PATHS == ("/fapi/v1/order", "/fapi/v1/order/test", "/fapi/v1/leverage", "/fapi/v1/marginType",
                             "/fapi/v1/algoOrder")                     # el stop de emergencia (servicio Algo)
    assert bf.DELETE_PATHS == ("/fapi/v1/order", "/fapi/v1/allOpenOrders", "/fapi/v1/algoOrder",
                               "/fapi/v1/algoOpenOrders")
    for path in ("/sapi/v1/asset/transfer", "/sapi/v1/futures/transfer", "/sapi/v1/capital/withdraw/apply",
                 "/fapi/v1/batchOrders", "/fapi/v1/positionSide/dual", "/fapi/v1/multiAssetsMargin"):
        assert not bf.allowed("POST", path), path                          # ni mover plata ni cambiar el modo
    from tradingagent.execution.live import mexc_futures as mf
    assert mf.GET_PATHS == ("/api/v1/private/account/assets", "/api/v1/private/position/open_positions",
                            "/api/v1/private/position/position_mode",           # solo LEER el modo (cobertura)
                            "/api/v1/private/order/get/{}", "/api/v1/private/order/deal_details/{}",
                            "/api/v1/private/order/list/open_orders/{}")
    assert mf.POST_PATHS == ("/api/v1/private/order/create", "/api/v1/private/order/cancel",
                             "/api/v1/private/order/cancel_all", "/api/v1/private/position/change_leverage")
    assert mf.ISOLATED == 1 and not mf.allowed("POST", "/api/v1/private/account/transfer")
    assert not mf.allowed("POST", "/api/v1/private/order/submit")          # la ruta vieja ya no está en la lista
    assert mf.TRADING_BASE == "https://api.mexc.com"                         # la dirección oficial desde ene-2026


def test_a3_el_cliente_firmado_de_mexc_solo_lee():
    from tradingagent.marketdata import mexc
    assert mexc.READ_ONLY_PATHS == {"/api/v1/private/account/assets", "/api/v1/private/account/tiered_fee_rate"}
    public = [n for n in dir(mexc.ReadOnlyClient) if not n.startswith("_")]
    assert sorted(public) == ["account_ok", "fee_rates", "get"]                 # ni post, ni put, ni delete
    src = (SRC / "marketdata" / "mexc.py").read_text(encoding="utf-8")
    assert not re.search(r"session\.(post|put|delete|patch)\(", src)


# -- B / D / E. Núcleo de seguridad ---------------------------------------------------------------
def _intent(**kw):
    return OrderIntent(client_id="x", symbol="BTCUSDT", side=Side.BUY, qty=D("1"), **kw)


def test_e1_d2_d6_el_nucleo_frena_lo_que_tiene_que_frenar():
    lim = limits.load()
    assert safety.intent_block_reasons(_intent(), killed=True, orders_last_min=0, limits=lim) == \
        ["interruptor_de_emergencia"]
    assert safety.intent_block_reasons(_intent(reduce_only=True), killed=True, orders_last_min=0, limits=lim) == []
    assert "tope_de_ordenes_por_minuto" in safety.intent_block_reasons(_intent(), killed=False, orders_last_min=30,
                                                                       limits=lim)
    assert safety.intent_block_reasons(_intent(max_slippage_bps=D("250")), killed=False, orders_last_min=0,
                                       limits=lim)[0].startswith("tope_de_precio_mayor_al_sobre")
    assert safety.slippage_cap(None, lim) == D("50") and safety.slippage_cap(D("20"), lim) == D("20")
    assert safety.slippage_cap(D("200"), lim) == D("200") and safety.slippage_cap(D("900"), lim) == D("200")
    assert safety.news_max_age(900, lim) == 600 and safety.news_max_age(120, lim) == 120


# -- G. Seguridad, secretos y separación de nexus -------------------------------------------------
TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
FAKE_TOKENS = {"123456789:AAFakeTokenForTestsOnly_abcdefghijklmn", "999999999:BADtokenBADtokenBADtokenBADtokenBAD12"}


def test_g1_sin_secretos_ni_env_en_el_repo():
    bad = []
    for f in repo_files():
        rel = f.relative_to(ROOT)
        if f.name == ".env" or (f.name.startswith(".env") and f.name != ".env.example"):
            bad.append(f"{rel}: archivo .env versionado")
            continue
        if f.suffix in {".png", ".jpg", ".ico", ".pyc"}:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----", text):
            bad.append(f"{rel}: clave privada")
        bad += [f"{rel}: token de Telegram" for t in TOKEN_RE.findall(text) if t not in FAKE_TOKENS]
    assert bad == []


def test_g5_trading_nunca_usa_las_carpetas_de_nexus(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_HOME", str(tmp_path / "nexus" / "algo"))
    with pytest.raises(paths.ForeignHomeError):
        paths.home()
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "otra"))
    monkeypatch.setenv("TRADING_HOME", str(tmp_path / "otra"))
    with pytest.raises(paths.ForeignHomeError):
        paths.home()
    monkeypatch.setenv("TRADING_HOME", str(tmp_path / "TRADING"))
    assert paths.home() == tmp_path / "TRADING"
    assert '"TRADING"' in (SRC / "paths.py").read_text(encoding="utf-8")      # %LOCALAPPDATA%\TRADING en Windows


def test_g5_el_codigo_no_referencia_nada_de_nexus():
    patterns = [r"NEXUS_HOME", r"(?i)localappdata\W{0,6}nexus", r"(?i)\.nexus\b", r"(?i)['\"]nexus-",
                r"(?im)^\s*(import|from)\s+nexus\b", r"(?i)userprofile\W{0,6}nexus", r"(?i)\\nexus\b"]
    hits = []
    deploy = [p for p in repo_files() if "deploy" in p.relative_to(ROOT).parts]
    assert deploy, "faltan los scripts de despliegue en la PC"
    for f in code_files() + deploy:
        if f.name == "paths.py" and f.parent == SRC:
            continue                                    # la guarda: nombra a nexus para negarse a usarlo
        text = f.read_text(encoding="utf-8")
        hits += [f"{f.relative_to(ROOT)}: {p}" for p in patterns if re.search(p, text)]
    assert hits == []


def test_g2_autoprueba_de_rutas_protegidas():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "protected_paths.py"), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# -- G-1 / G-4 / G-6. Workflows de GitHub Actions ---------------------------------------------------
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
# Solo credenciales sin poder sobre el dinero (REGLAS G-1): el bot de Telegram, el chat, el User-Agent de
# la SEC, el token propio del workflow, la clave de Claude (gasto con tope del sobre) y las claves de SOLO
# LECTURA de MEXC (sin trading ni retiros; el cliente solo hace GET a una lista cerrada). Nunca claves de
# trading ni de retiro. GitHub no distingue mayúsculas en los nombres de los secretos: acá tampoco.
ALLOWED_SECRETS = {"CHARBIS_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SEC_USER_AGENT", "GITHUB_TOKEN", "CLAUDE_API_KEY",
                   "ACCESS_KEY_MEXC", "SECRET_KEY_MEXC"}


def test_g1_los_workflows_solo_usan_secretos_sin_poder_sobre_el_dinero():
    assert WORKFLOWS
    text = "\n".join(f.read_text(encoding="utf-8") for f in WORKFLOWS)
    used = {u.upper() for u in re.findall(r"secrets\.([A-Za-z0-9_]+)", text)}
    assert used <= ALLOWED_SECRETS, sorted(used - ALLOWED_SECRETS)
    assert not re.search(r"secrets\s*\[|toJSON\(\s*secrets", text)       # nada de volcar todos los secretos
    assert not re.search(r"(?i)echo[^\n]*secrets\.", text)                # ni imprimirlos


def test_g4_acciones_fijadas_por_sha_y_permisos_minimos():
    for f in WORKFLOWS:
        text = f.read_text(encoding="utf-8")
        for ref in re.findall(r"uses:\s*([^\s#]+)", text):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{f.name}: {ref} sin SHA fijo"
        assert re.search(r"(?m)^permissions:\s*\{contents: read\}", text), f"{f.name}: permisos por defecto"
        writes = re.findall(r"([\w-]+):\s*write", text)
        if f.name == "recoleccion.yml":                                   # solo guarda datos en la rama data
            assert writes == ["contents"], writes
            assert re.findall(r"git push[^\n]*", text) == ["git push -q origin HEAD:data"]
        else:
            assert writes == [], f"{f.name}: {writes}"


def test_g6_trabajos_cortos_y_solo_en_este_repo():
    for f in WORKFLOWS:
        wf = yaml.safe_load(f.read_text(encoding="utf-8"))
        for name, job in wf["jobs"].items():
            assert 0 < job.get("timeout-minutes", 0) <= 15, f"{f.name}:{name} sin tope de tiempo corto"
        trigger = wf.get("on", wf.get(True)) or {}
        if isinstance(trigger, dict) and "schedule" in trigger:
            assert all(job.get("if") == "github.repository == 'bbarrionuevo/Trading'" for job in wf["jobs"].values())
            assert wf.get("concurrency", {}).get("cancel-in-progress") is False    # una corrida a la vez, sin cortes
            assert all(job.get("timeout-minutes", 0) <= 10 for job in wf["jobs"].values())


def test_g6_en_actions_solo_paper():
    wf = (ROOT / ".github" / "workflows" / "recoleccion.yml").read_text(encoding="utf-8")
    assert "--paper" in wf and not re.search(r"(?i)--(live|real)\b|mode:\s*live", wf)
    assert config.DEFAULTS["mode"] == "paper"


# -- G-7 / G-8. La PC: actualización automática, lo que se publica y el tablero -------------------------
def test_g7_la_pc_solo_toma_versiones_con_ci_verde_y_sin_posiciones():
    src = (SRC / "supervisor.py").read_text(encoding="utf-8")
    body = src[src.index("    def check_update"):src.index("    def run_tests")]
    assert body.index("ci_green(") < body.index("esperando_cierre") < body.index("self.move_to(remote)")
    assert "self.move_to(head)" in body                                     # y vuelve atrás si no arranca
    task = (ROOT / "deploy" / "windows" / "instalar-agente.ps1").read_text(encoding="utf-8")
    assert '"-m tradingagent supervisar"' in task and "-MultipleInstances IgnoreNew" in task


def test_g8_lo_publicado_va_solo_a_la_rama_pc_y_el_ci_la_ignora():
    src = (SRC / "supervisor.py").read_text(encoding="utf-8")
    pushes = re.findall(r'git\("push".*', src)
    assert pushes and all('"HEAD:refs/heads/pc"' in p for p in pushes)
    assert re.findall(r"refs/heads/(\w+)", src) == ["main", "pc"]           # lee main, escribe solo pc
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert {"data", "pc"} <= set(ci.get("on", ci.get(True))["push"]["branches-ignore"])


def test_g8_el_tablero_es_local_y_de_solo_lectura(tmp_path):
    import urllib.error
    import urllib.request

    from tradingagent import dashboard
    srv = dashboard.start(tmp_path, 0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
        base = dashboard.url(srv)
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(base + "/", method=method), timeout=5)
            assert e.value.code == 405
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/api/estado", headers={"Host": "evil.example"}),
                                   timeout=5)
        assert e.value.code == 403
    finally:
        srv.shutdown()
        srv.server_close()
