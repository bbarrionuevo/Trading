"""Latido del PC (cada 5 min): publica un resumen del estado en la rama `pc-status` de GitHub.

El vigilante de GitHub Actions y la sesión de Claude lo leen. Antes de publicar, ejecuta los comandos
de seguridad (issues con la etiqueta `pc-comando`, nexus/remote.py) y, cada 30 min, el chequeo previo del dinero
real (nexus/preflight.py), así el resultado sale en este mismo latido.
Nunca incluye secretos: estado, órdenes propias (IDs y precios) y advertencias del log, filtradas.
Si el push falla (sin credenciales de git), no rompe nada: queda registrado y sigue.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "local"))
from nexus import paths  # noqa: E402

SECRET_LIKE = re.compile(r"[A-Za-z0-9+/=_-]{28,}")


def redact(line):
    return SECRET_LIKE.sub("[oculto]", line)


def log_tail(n=20):
    p = paths.logs("agent.log")
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    return [redact(x) for x in lines if " WARNING " in x or " ERROR " in x][-n:]


def file_tail(name, n):
    """Últimas n líneas de un log (con cadenas tipo secreto ocultas) para diagnosticar a distancia."""
    p = paths.logs(name)
    if not p.exists():
        return []
    data = p.read_bytes()[-20000:].decode("utf-8", errors="replace")
    return [redact(x) for x in data.splitlines()[-n:]]


def trades_tail(n=4000, max_bytes=1_500_000):
    """Últimas vueltas del modelo vigente, compactas, para analizar pérdidas desde la nube.
    Formato por vuelta: [ts, par, pnl, tipo, segundos, entrada, salida, lab, spread %, volatilidad
    %/min, efficiency ratio, BTC 15 min %, estrategia] (clima al entrar). Sin secretos."""
    from nexus import evidence
    p = paths.state("trades.jsonl")
    if not p.exists():
        return []
    with open(p, "rb") as f:
        f.seek(0, 2)
        start = max(0, f.tell() - max_bytes)
        f.seek(start)
        lines = f.read().decode("utf-8", errors="replace").splitlines()[1 if start else 0:]   # 1ª puede venir cortada
    out = []
    for line in lines:
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if t.get("model", 1) >= evidence.FILL_MODEL:
            out.append([round(t["ts"]), t["sym"], t["pnl"], t.get("kind"), t.get("hold_s"), t.get("entry"),
                        t.get("exit"), int(bool(t.get("lab"))), t.get("spread_pct"), t.get("vol_1m_pct"),
                        t.get("er"), t.get("btc_ret15_pct"), t.get("strategy"), t.get("sel")])
    return out[-n:]


def jsonl_tail(name, n):
    """Últimas n líneas de un diario JSONL del estado (órdenes reales, vueltas reales)."""
    p = paths.state(name)
    if not p.exists():
        return []
    out = []
    for line in p.read_bytes()[-200_000:].decode("utf-8", errors="replace").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def real_block():
    """Lo del dinero real que no está en status.json: autorización, órdenes y vueltas recientes."""
    from nexus.agent.hedge_live import HEDGE_AUTH, HEDGE_ORDERS, HEDGE_TRADES
    from nexus.agent.live import AUTH_FILE, ORDERS_LOG, REAL_TRADES
    return {"auth": paths.read_json(paths.state(AUTH_FILE), None), "orders_tail": jsonl_tail(ORDERS_LOG, 60),
            "real_trades_tail": jsonl_tail(REAL_TRADES, 50), "risk_real": paths.read_json(paths.state("risk_real.json"), None),
            "preflight": paths.read_json(paths.state("preflight.json"), None),
            "hedge_auth": paths.read_json(paths.state(HEDGE_AUTH), None), "hedge_orders_tail": jsonl_tail(HEDGE_ORDERS, 40),
            "hedge_trades_tail": jsonl_tail(HEDGE_TRADES, 50), "risk_hedge": paths.read_json(paths.state("risk_hedge.json"), None)}


def build():
    kill = paths.kill_file()
    alloc = paths.read_json(paths.state("allocator.json"), {})
    return {"ts": time.time(), "status": paths.read_json(paths.state("status.json"), {}),
            "update": paths.read_json(paths.state("update.json"), {}), "log_tail": log_tail(),
            "runner_tail": file_tail("runner.log", 10), "stderr_tail": file_tail("agent_stderr.log", 30),
            "kill_switch": kill.exists(),
            "kill_text": redact(kill.read_text(encoding="utf-8", errors="ignore")[:120]) if kill.exists() else None,
            "trades_tail": trades_tail(), "real": real_block(),
            "remote": paths.read_json(paths.state("remote.json"), {}).get("done", {}),
            "allocator": {"promoted": alloc.get("promoted"), "history": (alloc.get("history") or [])[-10:]},
            "selector": {k: v[-10:] if k == "history" else v
                         for k, v in paths.read_json(paths.state("selector.json"), {}).items()}}


def housekeeping(repo_dir, now=None):
    """Comandos remotos de seguridad y chequeo previo (cada 30 min). Un error acá no frena el latido."""
    from nexus import preflight, remote
    now = now or time.time()
    paths.load_env()                                                 # claves para el preflight (no se publican)
    try:
        remote.process(remote.fetch(repo_dir), now)
    except Exception as e:                                           # noqa: BLE001
        print("comandos remotos: error", type(e).__name__)
    try:
        last = (paths.read_json(paths.state("preflight.json"), {}) or {}).get("ts", 0)
        if now - last >= 1800:
            preflight.run(now=now)
    except Exception as e:                                           # noqa: BLE001
        print("preflight: error", type(e).__name__)


def locked(max_age_s=240):
    """Evita dos latidos a la vez (la tarea de 5 min y el mantenimiento de 30 min)."""
    lock = paths.state("latido.lock")
    if lock.exists() and time.time() - lock.stat().st_mtime < max_age_s:
        return True
    lock.write_text(str(time.time()))
    return False


def git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


def main():
    if locked():
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "otro latido en curso: se omite")
        return
    try:
        publish()
    finally:
        paths.state("latido.lock").unlink(missing_ok=True)


def publish():
    d = paths.home() / "status-repo"
    d.mkdir(exist_ok=True)
    if not (d / ".git").exists():
        git(d, "init", "-q")
        git(d, "remote", "add", "origin", git(REPO, "remote", "get-url", "origin").stdout.strip())
    housekeeping(d)
    (d / "pc_status.json").write_text(json.dumps(build(), indent=1, default=str), encoding="utf-8")
    git(d, "checkout", "-q", "--orphan", f"tmp{int(time.time())}")
    git(d, "add", "pc_status.json")
    git(d, "-c", "user.name=nexus-pc", "-c", "user.email=nexus-pc@users.noreply.github.com",
        "commit", "-q", "-m", "latido del PC")
    r = git(d, "push", "-q", "-f", "origin", "HEAD:pc-status")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "latido publicado" if r.returncode == 0
          else f"no se pudo publicar el latido (¿credenciales de git?): {r.stderr.strip()[:200]}")
    for b in git(d, "branch", "--format=%(refname:short)").stdout.split():
        if b.startswith("tmp") and b != git(d, "branch", "--show-current").stdout.strip():
            git(d, "branch", "-q", "-D", b)


if __name__ == "__main__":
    main()
