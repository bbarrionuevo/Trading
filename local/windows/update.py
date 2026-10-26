"""Actualizador seguro del PC (R-12, R-13). Lo ejecuta la tarea programada cada 30 minutos.

Solo mira la rama main. Activa una versión nueva únicamente si:
  1. el CI de GitHub para ese commit terminó en verde;
  2. no toca rutas protegidas (categoría C), o el usuario la aprobó EN ESTA PC con --aprobar;
  3. el agente no tiene posiciones abiertas;
  4. los tests pasan en esta PC.
Después pide un reinicio controlado y verifica la salud; si falla, vuelve a la versión anterior.

python local/windows/update.py            ciclo normal
python local/windows/update.py --aprobar  aprobar a mano una versión con cambios protegidos
"""
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "local"))
from nexus import paths  # noqa: E402

# Las reglas de la versión INSTALADA juzgan a la candidata (la nueva no puede aflojarlas).
PROTECTED_PATHS_SCRIPT = REPO / "backtest" / "protected_paths.py"
OWNER_REPO = "bbarrionuevo/nexus"
BRANCH = "main"


def git(*args, check=True, timeout=180):
    # Con tiempo máximo: un fetch colgado (red) no debe trabar el mantenimiento para siempre.
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def ci_green(sha):
    import requests
    try:
        r = requests.get(f"https://api.github.com/repos/{OWNER_REPO}/commits/{sha}/check-runs",
                         headers={"Accept": "application/vnd.github+json"}, timeout=15)
        runs = [c for c in r.json().get("check_runs", []) if c.get("name") == "test"]
    except Exception:
        return False
    return bool(runs) and all(c["status"] == "completed" and c["conclusion"] == "success" for c in runs)


def protected_changes(cur, new):
    spec = importlib.util.spec_from_file_location("pp", PROTECTED_PATHS_SCRIPT)
    pp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pp)
    cwd = Path.cwd()
    try:
        import os
        os.chdir(REPO)
        try:
            return pp.violations(cur, new, runtime=True)   # los documentos no requieren aprobación local
        except TypeError:
            return pp.violations(cur, new)                 # versión instalada anterior a este cambio
    finally:
        os.chdir(cwd)


def is_ancestor(a, b):
    return subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def approval_covers(cur, new, approvals):
    """¿Alguna aprobación local cubre a la candidata?

    Vale la aprobación exacta de `new`, o la de un commit A con cur <= A <= new tal que de A a new no
    cambió nada protegido: lo protegido en new es idéntico a lo que el usuario aprobó. Así, si main
    avanza con cambios de categoría B antes de que el PC instale lo aprobado, no hay que re-aprobar.
    Una aprobación más vieja que la versión instalada no cuenta (no permite volver atrás).
    """
    if new in approvals:
        return True
    for a in approvals:
        try:
            if is_ancestor(cur, a) and is_ancestor(a, new) and not protected_changes(a, new):
                return True
        except Exception:
            continue
    return False


def open_positions():
    """Posiciones abiertas del PAPER, solo si el agente está vivo.

    Si el agente está detenido, su estado guardado no representa posiciones vigentes (son simuladas)
    y bloquear la actualización impediría instalar justamente el arreglo (bloqueo circular visto el
    24-oct-2026). ANTES DE LA FASE 6 esto debe reemplazarse por la conciliación con el exchange (R-05).
    """
    if not agent_alive(600):
        return []
    books = paths.read_json(paths.state("paper_books.json"), {})
    return [k for k, v in books.items() if v.get("state") == "long"]


def blockers(ci_ok, protected, approved, positions):
    """Decisión pura (testeable): motivos para NO activar la versión nueva."""
    r = []
    if not ci_ok:
        r.append("el CI de ese commit no está en verde")
    if protected and not approved:
        r.append("toca rutas protegidas y no fue aprobada en esta PC: " + ", ".join(protected))
    if positions:
        r.append("hay posiciones abiertas: " + ", ".join(positions))
    return r


def run_tests(timeout=900):
    """Tests de la candidata en esta PC. Si tardan más de `timeout` s cuentan como fallidos (rollback):
    un test colgado no puede dejar trabado el mantenimiento ni activar una versión sin probar."""
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                           cwd=REPO / "local", capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"los tests superaron {timeout} s (¿alguno colgado?)"
    return r.returncode == 0, r.stdout[-2000:]


def agent_alive(max_age=120):
    s = paths.read_json(paths.state("status.json"), {})
    return s and time.time() - s.get("ts", 0) < max_age


def healthy(new, t0, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        s = paths.read_json(paths.state("status.json"), {})
        if s.get("ts", 0) > t0 and str(new).startswith(str(s.get("version", "x"))):
            return True
        time.sleep(10)
    return False


def record(**kw):
    paths.write_json_atomic(paths.state("update.json"), {"ts": time.time(), **kw})
    print(time.strftime("%Y-%m-%d %H:%M:%S"), kw)


def candidate():
    """(actual, candidata, cambios protegidos, log) sin aprobar nada: lo muestra el tablero."""
    git("fetch", "-q", "origin", BRANCH)
    cur, new = git("rev-parse", "HEAD"), git("rev-parse", f"origin/{BRANCH}")
    return cur, new, protected_changes(cur, new) if cur != new else [], git("log", "--oneline", f"{cur}..{new}")


def approve(ask=input, out=print):
    """Aprobación local del dueño: desde la consola o desde el tablero (misma frase exacta)."""
    cur, new, prot, history = candidate()
    out(f"Versión candidata {new[:7]} (actual {cur[:7]}). Cambios protegidos: {prot or 'ninguno'}")
    out(history)
    phrase = f"APRUEBO {new[:7]}"
    if ask(f'Escribí exactamente "{phrase}" para aprobarla: ').strip() != phrase:
        out("No se aprobó nada.")
        return 1
    ap = paths.read_json(paths.state("approvals.json"), {})
    ap[new] = {"ts": time.time(), "protected": prot}
    paths.write_json_atomic(paths.state("approvals.json"), ap)
    out("Aprobada. Se activará en el próximo ciclo si el CI está verde y no hay posiciones abiertas.")
    return 0


def main():
    git("fetch", "-q", "origin", BRANCH)
    cur, new = git("rev-parse", "HEAD"), git("rev-parse", f"origin/{BRANCH}")
    if cur == new:
        return record(result="al día", version=cur[:7])
    prot = protected_changes(cur, new)
    approved = approval_covers(cur, new, paths.read_json(paths.state("approvals.json"), {}))
    why = blockers(ci_green(new), prot, approved, open_positions())
    if why:
        return record(result="pospuesta", current=cur[:7], candidate=new[:7], reasons=why)
    was_alive = agent_alive()
    reqs_changed = bool(git("diff", "--name-only", cur, new, "--", "local/requirements.txt"))
    git("checkout", "-q", "--detach", new)
    if reqs_changed:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(REPO / "local/requirements.txt")])
    ok, out = run_tests()
    if not ok:
        git("checkout", "-q", "--detach", cur)
        return record(result="rollback: tests fallaron en esta PC", current=cur[:7], candidate=new[:7], tests=out)
    t0 = time.time()
    paths.state("RESTART").touch()
    if was_alive and not healthy(new, t0):
        git("checkout", "-q", "--detach", cur)
        paths.state("RESTART").touch()
        return record(result="rollback: el agente no respondió con la versión nueva", current=cur[:7],
                      candidate=new[:7])
    return record(result="activada", previous=cur[:7], version=new[:7], health_checked=bool(was_alive))


if __name__ == "__main__":
    sys.exit(approve() if "--aprobar" in sys.argv else (main() and 0))
