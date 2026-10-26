"""Canal de comandos de SEGURIDAD: de la sesión de Claude en la nube al PC, por issues de GitHub.

Un comando es un issue ABIERTO con la etiqueta `pc-comando`, creado por la cuenta dueña del repositorio
(nadie de afuera puede crearlo con esa cuenta ni ponerle etiquetas). El título es el comando y el
cuerpo, el motivo. El latido (cada 5 min) los lee de la API pública de GitHub (sin credenciales) y
ejecuta cada uno UNA vez; la sesión de Claude cierra el issue al ver el resultado en el latido. Solo existen
comandos que reducen riesgo o piden información. Ninguno puede autorizar dinero real, desbloquear,
rearmar, vender a mercado ni cambiar límites (eso es solo del dueño, en la PC):
- pausar [motivo]: no abrir posiciones (KILL nivel 1, marcado "remoto").
- cancelar [motivo]: además cancelar todas las órdenes reales en MEXC (KILL nivel 2, "remoto").
- reanudar: deshace SOLO lo que puso este canal. Un KILL del dueño queda como estaba.
- diagnostico: corre el chequeo previo (preflight) ahora.
- latido: nada (sirve para confirmar que el canal funciona).
Un comando con más de 6 h se ignora (no se ejecutan órdenes viejas). El resultado va en el latido.
El dueño apaga el canal creando el archivo state/remote_off (sus comandos se registran sin ejecutarse).
"""
import calendar
import subprocess
import time

from . import paths

LABEL, MAX_AGE_S = "pc-comando", 6 * 3600
ALLOWED = ("pausar", "cancelar", "reanudar", "diagnostico", "latido")
REMOTE_TAG = "remoto"


def owner_repo(repo_dir):
    """("dueño", "repo") a partir del remoto de git (https://github.com/dueño/repo.git)."""
    url = subprocess.run(["git", "-C", str(repo_dir), "remote", "get-url", "origin"], capture_output=True,
                         text=True, timeout=20).stdout.strip()
    parts = url.rstrip("/").removesuffix(".git").split("/")
    return parts[-2], parts[-1]


def fetch(repo_dir, get=None):
    """Comandos pendientes: issues abiertos con la etiqueta, creados por el dueño ([] si no hay red)."""
    import requests
    owner, repo = owner_repo(repo_dir)
    get = get or (lambda url, params: requests.get(url, params=params, timeout=20,
                                                   headers={"Accept": "application/vnd.github+json"}).json())
    try:
        issues = get(f"https://api.github.com/repos/{owner}/{repo}/issues",
                     {"labels": LABEL, "state": "open", "creator": owner, "per_page": 30})
    except Exception:                                                # noqa: BLE001
        return []
    out = []
    for i in issues if isinstance(issues, list) else []:
        labels = [x.get("name") for x in i.get("labels") or []]
        if "pull_request" in i or (i.get("user") or {}).get("login") != owner or LABEL not in labels:
            continue
        words = (i.get("title") or "").strip().lower().split()
        try:
            ts = calendar.timegm(time.strptime(i["created_at"], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, ValueError):
            ts = None
        out.append({"id": f"issue{i['number']}", "cmd": words[0] if words else "", "ts": ts,
                    "motivo": (i.get("body") or "")[:200]})
    return out


def _prev_path():
    return paths.state("remote_kill_prev.txt")


def execute(c):
    """(ok, mensaje). Nunca relaja una protección que no haya puesto este mismo canal."""
    kill, cmd, why = paths.kill_file(), c.get("cmd"), str(c.get("motivo") or "")[:200]
    current = kill.read_text(encoding="utf-8", errors="ignore").strip() if kill.exists() else None
    if cmd in ("pausar", "cancelar"):
        level = "1" if cmd == "pausar" else "2"
        have = None if current is None else 3 if current.startswith("3") else 2 if current.startswith("2") else 1
        if have is not None and have >= int(level):
            return True, f"ya había un KILL igual o más fuerte ({current[:40] or 'del dueño'}): no se toca"
        if current is not None and REMOTE_TAG not in current:
            _prev_path().write_text(current, encoding="utf-8")      # se restaura al reanudar
        kill.write_text(f"{level} {REMOTE_TAG} {why}", encoding="utf-8")
        return True, f"KILL nivel {level} puesto"
    if cmd == "reanudar":
        if current is None:
            return True, "no había KILL"
        if REMOTE_TAG not in current:
            return False, "el KILL es del dueño: este canal no lo puede quitar"
        prev = _prev_path()
        if prev.exists():
            kill.write_text(prev.read_text(encoding="utf-8"), encoding="utf-8")
            prev.unlink()
            return True, "se restauró el KILL anterior del dueño"
        kill.unlink()
        return True, "KILL remoto quitado"
    if cmd == "diagnostico":
        from . import preflight
        r = preflight.run()
        return True, f"preflight: {'listo' if r['ready'] else 'no listo'}; problemas: {r['problems'][:3]}"
    if cmd == "latido":
        return True, "canal OK"
    return False, f"comando no permitido: {cmd!r}"


def process(commands, now=None):
    """Ejecuta los comandos nuevos y vigentes; guarda y devuelve los últimos resultados."""
    now = now or time.time()
    st = paths.read_json(paths.state("remote.json"), {"done": {}})
    done = st.setdefault("done", {})
    off = paths.state("remote_off").exists()
    for c in commands:
        cid = str(c.get("id") or "")
        if not cid or cid in done:
            continue
        if not isinstance(c.get("ts"), (int, float)) or now - c["ts"] > MAX_AGE_S or c["ts"] > now + 600:
            done[cid] = {"ts": now, "cmd": c.get("cmd"), "ok": False, "msg": "ignorado: vencido o sin fecha"}
            continue
        if off:
            done[cid] = {"ts": now, "cmd": c.get("cmd"), "ok": False, "msg": "canal apagado por el dueño"}
            continue
        try:
            ok, msg = execute(c) if c.get("cmd") in ALLOWED else (False, f"comando no permitido: {c.get('cmd')!r}")
        except Exception as e:                                       # noqa: BLE001
            ok, msg = False, f"error: {type(e).__name__}"
        done[cid] = {"ts": now, "cmd": c.get("cmd"), "ok": ok, "msg": msg}
    st["done"] = dict(sorted(done.items(), key=lambda kv: kv[1]["ts"])[-50:])
    paths.write_json_atomic(paths.state("remote.json"), st)
    return st["done"]
