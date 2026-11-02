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


def test_a2_esta_version_solo_opera_en_paper():
    assert config.DEFAULTS["mode"] == "paper"
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["mode"] = "live"
    assert any("solo opera en paper" in e for e in config.validate(cfg))
    assert not (SRC / "execution" / "live").exists()


def test_a3_sin_codigo_de_ordenes_reales_retiros_ni_firmas_de_exchange():
    banned = [r"/api/v3/order\b", r"/fapi/v\d/order", r"\bnewOrder\b", r"\bplaceOrder\b", r"capital/withdraw",
              r"\bwithdraw(al)?s?\s*\(", r"X-MBX-APIKEY", r"X-MEXC-APIKEY", r"\bccxt\b"]
    hits = []
    for f in code_files():
        text = f.read_text(encoding="utf-8")
        hits += [f"{f.relative_to(ROOT)}: {b}" for b in banned if re.search(b, text)]
    assert hits == []


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
    assert safety.intent_block_reasons(_intent(max_slippage_bps=D("80")), killed=False, orders_last_min=0,
                                       limits=lim)[0].startswith("tope_de_precio_mayor_al_sobre")
    assert safety.slippage_cap(None, lim) == D("50") and safety.slippage_cap(D("20"), lim) == D("20")
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
                r"(?im)^\s*(import|from)\s+nexus\b", r"(?i)userprofile\W{0,6}nexus"]
    hits = []
    for f in code_files():
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
# Solo credenciales sin poder sobre el dinero: el bot de Telegram, el chat, el User-Agent de la SEC y el
# token propio del workflow. Nunca claves de exchange (ni de solo lectura) mientras no hagan falta.
ALLOWED_SECRETS = {"CHARBIS_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SEC_USER_AGENT", "GITHUB_TOKEN"}


def test_g1_los_workflows_solo_usan_secretos_sin_poder_sobre_el_dinero():
    assert WORKFLOWS
    text = "\n".join(f.read_text(encoding="utf-8") for f in WORKFLOWS)
    used = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
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
