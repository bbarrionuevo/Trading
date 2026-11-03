"""Supervisor del agente en la PC (lo corre la tarea programada `trading-agente`): mantiene al agente vivo,
lo actualiza solo, sirve el tablero y publica el estado para que Claude lo vea.

  python -m tradingagent supervisar [--puerto 18765]

- **Siempre listo:** corre `python -m tradingagent run` como proceso hijo y lo relanza apenas termina (con
  espera creciente si se cae seguido; si se cae 3 veces en 10 minutos, avisa por Telegram). En Windows el
  agente va en un "job" que lo cierra si el supervisor muere de golpe, y un candado por carpeta impide que
  corran dos agentes sobre el mismo estado (`core/instance.py`).
- **Se actualiza solo (REGLAS G-7):** cada 15 minutos mira si hay una versión nueva en `main` del repo
  público. Solo la toma si el CI de GitHub de ese commit (corrida "push" de ci.yml) terminó en verde, y
  espera a que no haya posiciones abiertas (como mucho 6 h). Para al agente con calma (archivo
  `state/parar`: posiciones y caja quedan guardadas), avanza el repo, reinstala si cambiaron las
  dependencias y prueba que la versión nueva arranque. Si falla, vuelve a la anterior, avisa y no
  reintenta ese commit. Si cambió el propio supervisor, sale con código 75 y la tarea lo relanza en un
  minuto con el código nuevo. Después corre todos los tests en segundo plano, con prioridad baja, sin
  claves y en una carpeta aparte: el resultado (pruebas reales en Windows) queda en `logs/tests.txt`.
- **Tablero:** http://127.0.0.1:18765 o el siguiente puerto libre (dashboard.py; nunca comparte un puerto
  con otro programa), solo lectura y solo desde esta PC. El acceso directo abre `tablero.html`.
- **Claude puede ver cómo va (REGLAS G-8):** cada 15 minutos publica en la rama `pc` del repo un único
  commit (la rama no crece) con el tablero en JSON, el latido, la cuenta paper, operaciones, decisiones
  recientes, el supervisor, los tests y los últimos avisos del log. Nunca el `.env`, secretos ni saldos
  reales; todo pasa por `redact` y sin rutas personales. Empieza cuando el CI ignora la rama `pc`.
"""
import base64
import json
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__, dashboard, paths
from .core.instance import InstanceLock
from .core.redact import SECRET_ENV_HINTS, redact

log = logging.getLogger("tradingagent.supervisor")
REPO = Path(__file__).resolve().parents[2]
SLUG = "bbarrionuevo/Trading"
API = f"https://api.github.com/repos/{SLUG}"
REMOTE = f"https://github.com/{SLUG}.git"
CI_WORKFLOW = ".github/workflows/ci.yml"
CHECK_EVERY_S, PUBLISH_EVERY_S, FIRST_CHECK_S, FIRST_PUBLISH_S = 900.0, 900.0, 60.0, 120.0
# Con algo armado (dinero real o simulacro) o con posiciones abiertas, Claude vigila casi en vivo (pedido del dueño,
# 3-nov: "voy a necesitar que estés conectado prácticamente de forma sincrónica con mi PC"): la práctica y la salud
# se publican cada 2 min y las correcciones se buscan cada 5. Lo publicado sigue sin la cuenta real (G-8).
PUBLISH_FAST_S, CHECK_FAST_S = 120.0, 300.0
MAX_DEFER_S = 6 * 3600             # con posiciones abiertas la actualización espera, como mucho 6 h
EXIT_RELOAD, EXIT_LOCKED = 75, 3
BACKOFF = (2, 5, 15, 30, 60)
STABLE_S = 600                     # un agente que anduvo 10 min se relanza sin espera
OUT_MAX_BYTES = 5_000_000
PUBLISH_DECISIONS = 1500
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOW_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
GIT = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if os.name == "nt" else "git")
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
UTF8 = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}   # Windows: la salida va a un archivo, no a la consola
SCRUB = ("TELEGRAM_", "ANTHROPIC_", "CLAUDE_", "MEXC_", "GITHUB_", "SEC_USER_AGENT", "WEBHOOK_", "TRADING_HOME",
         "GIT_CONFIG")                     # la familia GIT_CONFIG_* va entera: a medias, git no arranca


def _run(cmd: list, *, cwd=None, timeout: float = 300, env=None, flags: int = 0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, creationflags=NO_WINDOW | flags)


def git(*args, repo: Path = REPO, timeout: float = 120, env: dict | None = None) -> str:
    r = _run([GIT, "-C", str(repo), *args], timeout=timeout, env={**os.environ, **GIT_ENV, **(env or {})})
    if r.returncode != 0:
        what = " ".join(a for a in args if not a.startswith("-") and "=" not in a)[:40]
        raise RuntimeError(redact(f"git {what}: {(r.stderr or r.stdout).strip()[:300]}"))
    return r.stdout.strip()


def http_json(url: str, token: str | None = None, timeout: float = 20):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "trading-agente"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return json.load(r)


def ci_green(sha: str, *, api: str = API, token: str | None = None) -> tuple[bool, str]:
    """¿Terminó en verde el CI (ci.yml, evento push) de este commit? Todas sus corridas, completas y verdes."""
    try:
        data = http_json(f"{api}/actions/runs?head_sha={sha}&event=push&per_page=50", token)
    except Exception as e:                                         # noqa: BLE001 (sin red: se reintenta después)
        return False, f"sin_respuesta:{type(e).__name__}"
    runs = [r for r in data.get("workflow_runs", []) if str(r.get("path", "")).split("@")[0] == CI_WORKFLOW
            and r.get("event") == "push" and r.get("head_sha") == sha]
    if not runs:
        return False, "sin_ci"
    if any(r.get("status") != "completed" for r in runs):
        return False, "en_curso"
    if any(r.get("conclusion") != "success" for r in runs):
        return False, "rojo"
    return True, "verde"


def rmtree(path: Path) -> None:
    """Borra una carpeta aunque tenga archivos de solo lectura (git los crea así en Windows)."""
    def retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if not Path(path).exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def change_title(subject: str, body: str = "") -> str:
    """Qué cambió, en una línea: el título del PR fusionado (sin "Merge pull request #N ...")."""
    m = re.match(r"Merge pull request #\d+(?::| from \S+)\s*(.*)", subject)
    if not m:
        return subject
    return m.group(1).strip() or next((x.strip() for x in body.splitlines() if x.strip()), "mejoras")


def notify(text: str, env=None) -> bool:
    """Telegram directo (el supervisor no usa el despachador del agente). Nunca lanza ni muestra el token."""
    env = env if env is not None else os.environ
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    base = (env.get("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")
    body = json.dumps({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}).encode()
    try:
        req = urllib.request.Request(f"{base}/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:                                         # noqa: BLE001 (Telegram nunca frena al supervisor)
        log.warning("Telegram falló: %s", redact(str(e), secrets=(token,))[:200])
        return False


def _kill_on_close_job():
    """Windows: un "job" que cierra al agente (y a los tests) si el supervisor muere de golpe."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000                             # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        return k32, job
    except Exception as e:                                         # noqa: BLE001 (sin job anda igual: hay candado)
        log.warning("sin job de Windows: %s", e)
        return None


def without_real(heartbeat_text: str) -> str:
    """G-8: el latido se publica sin la cuenta de DINERO REAL (saldo, posiciones, resultado). El simulacro sí
    se publica: es plata de mentira."""
    try:
        hb = json.loads(heartbeat_text)
    except ValueError:
        return "{}"
    real = hb.get("real") if isinstance(hb, dict) else None
    if isinstance(real, dict) and not real.get("simulacro"):
        hb["real"] = {"oculto": "cuenta de dinero real: no se publica (REGLAS G-8)"}
    return json.dumps(hb, ensure_ascii=False, indent=1, default=str)


class Supervisor:
    def __init__(self, *, repo: Path = REPO, home: Path | None = None, python: str = sys.executable, api: str = API,
                 remote: str = REMOTE, child_cmd: list | None = None, env=None, port: int | None = dashboard.PORT):
        self.repo, self.home, self.python, self.api, self.remote = Path(repo), home or paths.home(), python, api, remote
        self.child_cmd = child_cmd or [python, "-m", "tradingagent", "run"]
        self.env = env if env is not None else os.environ
        self.port = port
        self.state_file = self.home / "state" / "supervisor.json"
        self.stop_file = self.home / "state" / "parar"
        self.agent_lock = InstanceLock(self.home / "state" / "agente.lock")
        self.state = paths.read_json(self.state_file, None) or {}
        self.child: subprocess.Popen | None = None
        self.tests: subprocess.Popen | None = None
        self.started_at = 0.0
        self.crashes: list[float] = []
        self.board = None
        self.job = _kill_on_close_job()
        self.version = self.head()

    # -- estado -----------------------------------------------------------------------------------------
    def save(self) -> None:
        """Nunca tumba al supervisor: en Windows el archivo puede estar abierto un instante por otro."""
        for i in range(5):
            try:
                paths.write_json_atomic(self.state_file, self.state)
                return
            except OSError as e:
                err = e
                time.sleep(0.05 * (i + 1))
        log.warning("no pude guardar supervisor.json (sigo; lo reintento después): %s", err)

    def event(self, kind: str, **data) -> None:
        rows = self.state.setdefault(kind, [])
        rows.append({"ts": round(time.time()), **data})
        del rows[:-50]
        self.save()

    def _contain(self, proc: subprocess.Popen) -> None:
        if self.job is not None:
            k32, job = self.job
            if not k32.AssignProcessToJobObject(job, int(proc._handle)):
                log.warning("no pude poner el proceso %s en el job", proc.pid)

    def busy(self) -> bool:
        """¿Hay algo armado o alguna posición abierta? Entonces se publica y se buscan correcciones más seguido."""
        return (self.home / "ARMADO.json").exists() or self.open_positions() > 0

    def open_positions(self) -> int:
        """Posiciones abiertas de todas las cuentas (práctica, simulacro y dinero real): con alguna abierta no se
        actualiza (salvo el máximo de espera), así un reinicio nunca cae en el medio de una operación."""
        return sum(len((dashboard.read_json(f, None) or {}).get("positions") or {})
                   for d in ("paper-vivo", "simulacro", "real") for f in (self.home / d).glob("*/estado.json"))

    # -- el agente --------------------------------------------------------------------------------------
    def adopt_orphan(self, timeout_s: float = 40) -> str:
        """Si quedó un agente sin supervisor (el anterior murió de golpe), se le pide parar con calma."""
        if not self.agent_lock.held_by_other():
            return "ninguno"
        self.stop_file.write_text("supervisor nuevo", encoding="utf-8")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.agent_lock.held_by_other():
                return "parado"
            time.sleep(0.5)
        try:
            os.kill(int((self.home / "state" / "agente.pid").read_text(encoding="utf-8").strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        for _ in range(20):
            if not self.agent_lock.held_by_other():
                return "terminado"
            time.sleep(0.5)
        return "sigue_vivo"

    def start_child(self) -> None:
        logs = self.home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out_path = logs / "agente-salida.log"
        try:
            if out_path.exists() and out_path.stat().st_size > OUT_MAX_BYTES:
                os.replace(out_path, logs / "agente-salida.1.log")
            self.stop_file.unlink(missing_ok=True)
        except OSError as e:                                       # nunca impide relanzar al agente
            log.warning("no pude rotar la salida del agente: %s", e)
        with open(out_path, "a", encoding="utf-8") as out:
            self.child = subprocess.Popen(self.child_cmd, cwd=self.repo, stdout=out, stderr=subprocess.STDOUT,
                                          env={**self.env, **UTF8}, creationflags=NO_WINDOW)
        self._contain(self.child)
        self.started_at = time.monotonic()
        self.state.update(arranques=self.state.get("arranques", 0) + 1, version=self.version[:12],
                          agente_desde=round(time.time()), supervisor=__version__)
        self.save()

    def stop_child(self, timeout_s: float = 30) -> None:
        """Parada con calma: el agente ve `state/parar`, cierra en orden y guarda; si no, se lo termina."""
        if self.child is None:
            return
        if self.child.poll() is None:
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.write_text("actualizacion", encoding="utf-8")
            try:
                self.child.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.child.terminate()
                try:
                    self.child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=10)
        self.child = None

    def watch_child(self) -> float:
        """Si el agente terminó, lo anota y devuelve cuánto esperar antes de relanzarlo."""
        if self.child is None or self.child.poll() is None:
            return 0.0
        code, alive = self.child.returncode, time.monotonic() - self.started_at
        self.child = None
        now = time.time()
        self.crashes = [t for t in self.crashes if now - t < 600] + [now]
        why = "otro_agente_corriendo" if code == EXIT_LOCKED else ""
        self.event("caidas", codigo=code, vivo_s=round(alive), motivo=why)
        log.warning("el agente terminó (código %s, vivió %.0f s): lo relanzo", code, alive)
        if len(self.crashes) >= 3 and now - self.state.get("aviso_caidas", 0) > 3600:
            detail = ("Hay otro TRADING abierto a mano en tu PC: cerralo, el automático ya está andando." if why
                      else "Si sigue pasando, lo reviso.")
            notify(f"⚠️ TRADING se cayó {len(self.crashes)} veces en 10 minutos y lo volví a levantar solo. {detail}",
                   self.env)
            self.state["aviso_caidas"] = round(now)
            self.save()
        return 0.0 if alive > STABLE_S else float(BACKOFF[min(len(self.crashes) - 1, len(BACKOFF) - 1)])

    # -- actualización ----------------------------------------------------------------------------------
    def head(self) -> str:
        try:
            return git("rev-parse", "HEAD", repo=self.repo)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return "?"

    def loaded_files(self) -> set[str]:
        """Archivos del repo que este proceso tiene cargados: si una versión nueva los cambia, se recarga."""
        out, root = set(), self.repo.resolve()
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", None)
            if f:
                try:
                    out.add(Path(f).resolve().relative_to(root).as_posix())
                except ValueError:
                    pass
        return out

    def smoke(self) -> tuple[bool, str]:
        """¿La versión nueva arranca? Importa todo y pide la versión (no toca dinero ni red)."""
        code = ("import tradingagent.app, tradingagent.live, tradingagent.supervisor, tradingagent.dashboard, "
                "tradingagent.research.collector")
        for cmd in ([self.python, "-c", code], [self.python, "-m", "tradingagent", "--version"]):
            r = _run(cmd, cwd=self.repo, timeout=120, env={**self.env, **UTF8})
            if r.returncode != 0:
                return False, redact((r.stderr or r.stdout).strip()[-400:])
        return True, ""

    def install(self) -> bool:
        r = _run([self.python, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", f"{self.repo}[dev]"],
                 cwd=self.repo, timeout=900)
        if r.returncode != 0:
            log.warning("pip falló: %s", redact(r.stderr[-300:]))
        return r.returncode == 0

    def fetch(self) -> None:
        git("fetch", "-q", self.remote, "+refs/heads/main:refs/remotes/origin/main", repo=self.repo, timeout=180)

    def move_to(self, sha: str) -> None:
        git("checkout", "-q", "-f", "-B", "main", sha, repo=self.repo)

    def check_update(self) -> str:
        """Una vuelta de actualización. Devuelve qué pasó (queda en el estado y en el tablero)."""
        if self.tests is not None and self.tests.poll() is None:
            return "tests_en_curso"
        try:
            self.fetch()
            head = git("rev-parse", "HEAD", repo=self.repo)
            remote = git("rev-parse", "refs/remotes/origin/main", repo=self.repo)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return f"sin_git:{str(e)[:120]}"
        if head == remote:
            self.state.pop("pendiente", None)
            return "al_dia"
        if remote == self.state.get("rechazada"):
            return "rechazada_antes"
        ok, why = ci_green(remote, api=self.api, token=self.env.get("GITHUB_TOKEN_PC"))
        if not ok:
            return f"esperando_ci:{why}"
        pending = self.state.get("pendiente") or {}
        if pending.get("sha") != remote:
            pending = self.state["pendiente"] = {"sha": remote, "desde": round(time.time())}
        n_open = self.open_positions()
        if n_open and time.time() - pending["desde"] < MAX_DEFER_S:
            return f"esperando_cierre:{n_open}"
        try:
            changed = set(git("diff", "--name-only", head, remote, repo=self.repo).splitlines())
            subject = git("log", "-1", "--format=%s", remote, repo=self.repo)
            body = git("log", "-1", "--format=%b", remote, repo=self.repo)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return f"sin_git:{str(e)[:120]}"
        reload = bool(changed & self.loaded_files())
        deps = "pyproject.toml" in changed
        self.stop_child()
        try:
            self.move_to(remote)
            if deps and not self.install():
                raise RuntimeError("pip install falló")
            ok, err = self.smoke()
            if not ok:
                raise RuntimeError(err)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            err = redact(str(e))[:300]
            try:
                self.move_to(head)
                if deps:
                    self.install()
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e2:
                log.error("no pude volver a %s: %s", head[:7], e2)
            self.state["rechazada"] = remote
            self.state.pop("pendiente", None)
            self.event("actualizaciones", de=head[:7], a=remote[:7], resultado="rechazada", error=err)
            notify(f"⚠️ La versión nueva de TRADING ({remote[:7]}) no arrancó en tu PC: sigo con la anterior, que "
                   "anda bien. No hace falta que hagas nada; quedó anotado para revisarlo.", self.env)
            return "rechazada"
        self.version = remote
        self.state.pop("pendiente", None)
        self.event("actualizaciones", de=head[:7], a=remote[:7], resultado="ok", titulo=subject[:120])
        notify(f"🔄 Actualicé TRADING (versión {remote[:7]}): {change_title(subject, body)[:150]}. Sigue "
               "funcionando solo.", self.env)
        return "recargar_supervisor" if reload else "actualizado"

    def run_tests(self) -> None:
        """Todos los tests en segundo plano: prioridad baja, sin claves y con su propia carpeta (nunca tocan el
        estado real ni mandan mensajes). Resultado en logs/tests.txt."""
        if self.tests is not None and self.tests.poll() is None:
            return
        env = {k: v for k, v in self.env.items()
               if not any(h in k.upper() for h in SECRET_ENV_HINTS) and not k.upper().startswith(SCRUB)}
        scratch = self.home / "pruebas"
        try:
            rmtree(scratch)
        except OSError as e:
            log.warning("no pude limpiar la carpeta de pruebas: %s", e)
        env.update(UTF8, TRADING_HOME=str(scratch))
        (self.home / "logs").mkdir(parents=True, exist_ok=True)
        with open(self.home / "logs" / "tests.txt", "w", encoding="utf-8") as out:
            self.tests = subprocess.Popen([self.python, "-m", "pytest", "-p", "no:cacheprovider", "-rfE"], cwd=self.repo,
                                          stdout=out, stderr=subprocess.STDOUT, env=env,
                                          creationflags=NO_WINDOW | LOW_PRIORITY)
        self._contain(self.tests)
        self.state.update(tests_version=self.version[:7], tests_desde=round(time.time()))
        self.save()

    def tests_done(self) -> None:
        if self.tests is not None and self.tests.poll() is not None:
            self.state["tests_resultado"] = {"ts": round(time.time()), "codigo": self.tests.returncode,
                                             "version": self.state.get("tests_version")}
            self.tests = None
            self.save()

    # -- publicación para Claude (REGLAS G-8) -----------------------------------------------------------
    def ci_ignores_pc(self) -> bool:
        try:
            text = (self.repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        except OSError:
            return False
        return re.search(r"branches-ignore:\s*\[[^\]]*\bpc\b[^\]]*\]", text) is not None

    def clean(self, text: str) -> str:
        """Sin secretos ni rutas personales (el usuario de Windows)."""
        out = redact(text)
        for p in sorted({str(self.home), str(self.repo), os.path.expanduser("~")}, key=len, reverse=True):
            if len(p) > 3:
                out = out.replace(p.replace("\\", "\\\\"), "~").replace(p, "~")
        return out

    def _put(self, dest: Path, text: str) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.clean(text), encoding="utf-8")

    def snapshot_files(self, dest: Path) -> None:
        """Lo que se publica: práctica, simulacro (plata de mentira) y salud. Nada del .env, del diario completo
        ni saldos reales."""
        board = dashboard.gather(self.home, repo=self.repo, include_real=False)   # G-8: nunca saldos reales
        self._put(dest / "tablero.json", json.dumps(board, ensure_ascii=False, indent=1, default=str))
        read = dashboard.read_text                                 # sin trabar al agente (Windows)
        for name in ("heartbeat.json", "supervisor.json"):
            text = read(self.home / "state" / name)
            if text is not None:
                self._put(dest / name, without_real(text) if name == "heartbeat.json" else text)
        for st in sorted([*(self.home / "paper-vivo").glob("*/estado.json"),
                          *(self.home / "simulacro").glob("*/estado.json")]):
            base, out = st.parent, dest / st.parent.parent.name / st.parent.name
            self._put(out / "estado.json", read(st) or "{}")
            for f in sorted((base / "operaciones").glob("*.jsonl")):
                self._put(out / "operaciones" / f.name, read(f) or "")
            rows = []
            for f in sorted((base / "decisiones").glob("*.jsonl"))[-2:]:
                rows += (read(f) or "").splitlines()
            self._put(out / "decisiones-recientes.jsonl", "\n".join(rows[-PUBLISH_DECISIONS:]) + "\n")
        learned = read(self.home / "datos" / "aprendizaje" / "estado.json")
        if learned is not None:
            self._put(dest / "aprendizaje.json", learned)
        tests = read(self.home / "logs" / "tests.txt")
        if tests is not None:
            self._put(dest / "tests.txt", tests[-20000:])
        lines = dashboard._tail(self.home / "logs" / "agent.log", 3_000_000)
        warn = [x for x in lines if '"WARNING"' in x or '"ERROR"' in x]
        self._put(dest / "errores.jsonl", "\n".join(warn[-200:]) + "\n")
        out_lines = dashboard._tail(self.home / "logs" / "agente-salida.log", 200_000)
        self._put(dest / "agente-salida.txt", "\n".join(out_lines[-200:]) + "\n")
        sup_lines = dashboard._tail(self.home / "logs" / "supervisor.log", 500_000)
        self._put(dest / "supervisor-log.jsonl", "\n".join(sup_lines[-200:]) + "\n")
        self._put(dest / "LEEME.md", "# Estado del agente en la PC del dueño\n\nLo publica el supervisor cada 15 min "
                  "(REGLAS G-8): solo paper y salud, sin secretos. Empezá por `tablero.json`.\n\n"
                  f"- Versión del programa: {self.version[:7]} (paquete {__version__})\n"
                  f"- Publicado: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    def publish(self) -> str:
        token = self.env.get("GITHUB_TOKEN_PC")
        if not token:
            return "sin_token"
        if not self.ci_ignores_pc():
            return "esperando_ci"
        auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env = {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
               "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}", "GIT_CONFIG_KEY_1": "credential.helper",
               "GIT_CONFIG_VALUE_1": ""}
        work = self.home / "publicar"
        try:
            rmtree(work)
            work.mkdir(parents=True)
            self.snapshot_files(work)
            git("init", "-q", "-b", "pc", repo=work)
            git("add", "-A", repo=work)
            git("-c", "user.name=trading-agente", "-c", "user.email=trading-agente@users.noreply.github.com",
                "-c", "commit.gpgsign=false", "commit", "-q", "-m",
                f"estado de la PC {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())}", repo=work)
            git("push", "-q", "--force", self.remote, "HEAD:refs/heads/pc", repo=work, timeout=180, env=env)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return "error:" + redact(str(e), secrets=(token, auth))[:200]
        return "ok"

    # -- bucle ------------------------------------------------------------------------------------------
    def can_reload(self) -> bool:
        """Salir para recargarse solo si la tarea lo vuelve a lanzar en un minuto (la marca la deja el
        instalador de la tarea). Si no, el agente quedaría apagado hasta reiniciar la PC."""
        return bool((dashboard.read_json(self.home / "state" / "tarea.json", None) or {}).get("cada_minuto"))

    def start_board(self) -> None:
        if self.port is None:                          # None = sin tablero; 0 = puerto libre (tests)
            return
        try:
            self.board = dashboard.start_free(self.home, self.port)
            self.state["tablero"] = dashboard.announce(self.home, self.board)
            log.info("tablero en %s", self.state["tablero"])
        except OSError as e:
            log.warning("no pude abrir el tablero desde el puerto %s: %s", self.port, e)

    def run(self, *, max_loops: int | None = None, tick_s: float = 1.0) -> int:
        log.info("supervisor %s en %s (versión %s)", __version__, self.repo, self.version[:7])
        self.start_board()
        orphan = self.adopt_orphan()
        if orphan != "ninguno":
            log.warning("había un agente sin supervisor: %s", orphan)
        self.start_child()
        next_update, next_publish = time.monotonic() + FIRST_CHECK_S, time.monotonic() + FIRST_PUBLISH_S
        loops = 0
        try:
            while max_loops is None or loops < max_loops:
                loops += 1
                wait = self.watch_child()
                if self.child is None:
                    time.sleep(wait)
                    self.start_child()
                self.tests_done()
                if self.tests is None and self.state.get("tests_version") != self.version[:7]:
                    self.run_tests()
                now = time.monotonic()
                if now >= next_update:
                    next_update = now + (CHECK_FAST_S if self.busy() else CHECK_EVERY_S)
                    result = self.check_update()
                    self.state["ultima_revision"] = {"ts": round(time.time()), "resultado": result}
                    self.save()
                    if result == "recargar_supervisor":
                        if self.can_reload():
                            log.info("cambió el supervisor: salgo para que la tarea me relance con el código nuevo")
                            return EXIT_RELOAD
                        log.warning("cambió el supervisor, pero sin la tarea de cada minuto no salgo: el agente ya "
                                    "corre la versión nueva y el supervisor se renueva al reiniciar la PC")
                        self.state["supervisor_pendiente"] = self.version[:7]
                    if self.child is None:
                        self.start_child()
                if now >= next_publish:
                    next_publish = now + (PUBLISH_FAST_S if self.busy() else PUBLISH_EVERY_S)
                    self.state["publicacion"] = {"ts": round(time.time()), "resultado": self.publish()}
                    self.save()
                time.sleep(tick_s)
            return 0
        finally:
            self.stop_child()                          # también con Ctrl+C: el agente cierra en orden
            if self.board is not None:
                self.board.shutdown()
                self.board.server_close()
