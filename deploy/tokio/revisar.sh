#!/usr/bin/env bash
# Revisión de Tokio: todo lo que tiene que estar para operar (pedido del dueño, 7-nov: "anotate algún procedimiento de
# instalación para que siempre revises las conexiones tibias y etc antes de instalar"; y "si algún día tengo que parar
# el programa o reiniciar la pc de tokio me gustaría que todo arranque solo y perfecto"). Solo lee: se puede correr
# siempre, antes y después de instalar, después de un reinicio y en cada vigilancia.
#
#   bash ~/trading/deploy/tokio/revisar.sh            revisa
#   bash ~/trading/deploy/tokio/revisar.sh --antes    además: con posiciones reales abiertas NO se instala (G-7)
#
# Cada línea dice OK, OJO (mirar) o FALLA (arreglar antes de seguir). Sale con 1 si hay alguna FALLA. Nunca muestra
# claves ni saldos.
set -uo pipefail
exec python3 - "$@" <<'PY'
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ANTES = "--antes" in sys.argv[1:]
HOME = Path("/home/ubuntu/.trading")
REPO = Path("/home/ubuntu/trading")
fallas, ojos = [], []


def sh(*cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def ok(msg):
    print(f"OK    {msg}")


def ojo(msg):
    ojos.append(msg)
    print(f"OJO   {msg}")


def falla(msg):
    fallas.append(msg)
    print(f"FALLA {msg}")


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def started_s(unit):
    """Hace cuántos segundos arrancó la unidad (None si no corre)."""
    raw = sh("systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", unit)
    try:
        since = int(raw) / 1e6
    except ValueError:
        return None
    up = float(Path("/proc/uptime").read_text().split()[0])
    return up - since if since > 0 else None


# 0. Recién arrancado (después de instalar o de un reinicio), el agente tarda ~1 min en leer sus fuentes, conectar el
#    canal de anuncios y abrir las conexiones tibias: se espera a que lleve 60 s andando (el 7-nov, a los 7 s de un
#    reinicio, todo eso aparecía como falla y al minuto estaba bien).
up = started_s("agente-tokio")
if up is not None and up < 60:
    print(f"esperando que el agente termine de arrancar ({60 - up:.0f} s)...", flush=True)
    time.sleep(60 - up)

# 1. Servicios: andando y habilitados para arrancar solos después de un reinicio (esperan la red y se relanzan solos)
for unit in ("rele-tokio", "agente-tokio", "vigia-red.timer"):
    active, enabled = sh("systemctl", "is-active", unit), sh("systemctl", "is-enabled", unit)
    restart = sh("systemctl", "show", "-p", "Restart", "--value", unit) if unit.endswith("tokio") else "always"
    if active == "active" and enabled == "enabled" and restart == "always":
        ok(f"{unit}: andando y arranca solo al reiniciar")
    else:
        falla(f"{unit}: {active}, {enabled}, Restart={restart} (tiene que ser active, enabled y always)")
if sh("systemctl", "is-enabled", "systemd-networkd-wait-online.service") != "enabled":
    ojo("systemd-networkd-wait-online no está habilitado: al reiniciar, el agente podría arrancar sin red")

# 2. Código
sh("git", "-C", str(REPO), "fetch", "-q", "origin", timeout=60)
head = sh("git", "-C", str(REPO), "rev-parse", "--short", "HEAD")
main = sh("git", "-C", str(REPO), "rev-parse", "--short", "origin/main")
if head and head == main:
    ok(f"código al día con main ({head})")
elif head and main:
    nuevos = sh("git", "-C", str(REPO), "log", "--oneline", "HEAD..origin/main")
    (ok if ANTES else ojo)(f"código {head}; main {main}: {len(nuevos.splitlines())} cambio(s) sin instalar")
else:
    falla("no pude leer la versión del código")

# 3. Memoria
avail = int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) // 1024
(falla if avail < 150 else ojo if avail < 250 else ok)(f"memoria disponible del servidor: {avail} MB")
try:
    agent_mb = int(sh("systemctl", "show", "-p", "MemoryCurrent", "--value", "agente-tokio")) // 2**20
    (ojo if agent_mb > 350 else ok)(f"memoria del agente: {agent_mb} MB (frena a los 400, se reinicia a los 500)")
except ValueError:
    falla("no pude leer la memoria del agente")

# 4. Armado y posiciones
now = time.time()
arm, sim = read(HOME / "ARMADO.json"), read(HOME / "SIMULACRO.json")
NUNCA = "nunca"                                  # armado sin vencimiento (REGLAS A-2, v3.10)
real = bool(arm) and not arm.get("simulacro") and (arm.get("vence") == NUNCA or float(arm.get("vence") or 0) > now)
if arm:
    dias = 1e9 if arm.get("vence") == NUNCA else (float(arm.get("vence") or 0) - now) / 86400
    que = "DINERO REAL" if not arm.get("simulacro") else "simulacro"
    est = ", ".join(arm.get("estrategias") or ["todas"])
    if dias <= 0:
        ojo(f"el armado ({que}) venció: no abre nada nuevo (armar es del dueño)")
    else:
        (ojo if dias < 3 else ok)(f"armado: {que} · {est} · "
                                  + ("sin vencimiento" if dias >= 1e9 else f"vence en {dias:.1f} días"))
else:
    ok("sin armado de dinero real")
side = bool(sim) and (sim.get("vence") == NUNCA or float(sim.get("vence") or 0) > now)
if sim:
    dias = 1e9 if sim.get("vence") == NUNCA else (float(sim.get("vence") or 0) - now) / 86400
    (ojo if dias < 3 else ok)(("simulacro junto al real: sin vencimiento" if dias >= 1e9 else
                               f"simulacro junto al real: vence en {dias:.1f} días") if dias > 0 else
                              "simulacro junto al real: venció (armar-real --simulacro --dias 30)")
check = read(HOME / "state" / "verificacion_binance.json")
if real:
    if check and check.get("ok"):
        ok(f"prueba sin riesgo de Binance pasada hace {(now - float(check.get('ts') or 0)) / 86400:.1f} días")
    else:
        ojo("sin prueba sin riesgo de Binance pasada (binance-real-check, la corre el dueño)")
for cuenta in ("real", "simulacro"):
    st = read(HOME / cuenta / "binance-futuros" / "estado.json")
    if st is None:
        continue
    n = len(st.get("positions") or {})
    if cuenta == "real" and n and ANTES:
        falla(f"{n} posición(es) REAL(es) abierta(s): no instalar ni reiniciar ahora (G-7); esperar a que cierren")
    else:
        ok(f"posiciones abiertas ({cuenta}): {n}")

# 5. El agente: latido, fuentes, canal de Binance, bucle
hb = read(HOME / "state" / "heartbeat.json") or {}
age = now - float(hb.get("ts") or 0)
(ok if age < 60 else falla)(f"latido del agente: hace {age:.0f} s")
src = hb.get("sources") or {}
if src:
    failing = src.get("failing") or []
    (ok if not failing and src.get("ok") == src.get("total") else ojo)(
        f"fuentes: {src.get('ok')} de {src.get('total')} andando" +
        (f" · fallan: {', '.join(map(str, failing))}" if failing else "" if src.get("ok") == src.get("total")
         else " (las demás todavía no leyeron)"))
ws = next((s for s in hb.get("streams") or [] if isinstance(s, dict) and "conectada" in s), None)
if ws is not None:
    (ok if ws.get("conectada") else falla)("canal de anuncios de Binance (WebSocket): " +
                                           ("conectado" if ws.get("conectada") else f"cortado ({ws.get('ultimo_error')})"))
lag = (hb.get("loop_lag") or {}).get("p99_ms")
if lag is not None:
    (ojo if lag > 50 else ok)(f"bucle del agente: p99 {lag} ms")

# 6. Conexiones tibias con Binance: 4 por grupo de conexiones (el del dinero real y el del simulacro), cada 10 s
pid = sh("systemctl", "show", "-p", "MainPID", "--value", "agente-tokio")
peers = []
for line in sh("ss", "-tnpH", "state", "established").splitlines():
    if f"pid={pid}," in line:
        dst = line.split()[3]
        if dst.endswith(":443"):
            peers.append(dst.rsplit(":", 1)[0].strip("[]"))
binance = 0
for ip in peers:
    name = sh("getent", "hosts", ip, timeout=3)
    binance += "ap-northeast-1.compute.amazonaws.com" in name     # Binance Futures está en AWS de Tokio
want = 4 * ((1 if real else 0) + (1 if (side or not real) else 0))
(ok if binance >= want else falla)(f"conexiones tibias con Binance: {binance} abiertas (hacen falta {want}: 4 por grupo)")

# 7. El relé: Telegram (los avisos de Upbit) y la PC
rpid = sh("systemctl", "show", "-p", "MainPID", "--value", "rele-tokio")
tg = sum(1 for line in sh("ss", "-tnpH", "state", "established").splitlines()
         if f"pid={rpid}," in line and re.search(r"\s(149\.154\.|91\.108\.|\[2001:67c:4e8:)", line))
since = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                  sh("systemctl", "show", "-p", "ActiveEnterTimestamp", "--value", "rele-tokio"))
log = sh("sudo", "-n", "journalctl", "-u", "rele-tokio", "--since", since.group(0) if since else "-1h", "--no-pager",
         "-q", timeout=30)
last = [x for x in log.splitlines() if "Telegram: escuchando" in x or "Telegram cortado" in x]
if tg and last and "escuchando" in last[-1]:
    ok("relé: escuchando el canal oficial de Upbit en Telegram")
else:
    falla("relé: Telegram no está conectado (los avisos de Upbit no llegan)")
pc = [x for x in log.splitlines() if "estado: PC" in x]          # su informe de cada 5 min
hello = "la PC habla desde" in log and "el agente de Tokio habla desde" in log   # o el saludo de cada uno al arrancar
if (pc and "agente de Tokio sí" in pc[-1]) or (not pc and hello):
    ok("relé: habla con la PC y con el agente")
else:
    ojo("relé: no informó que habla con la PC y con el agente")

# 8. Errores de los últimos 15 minutos y pausa
errs = sh("sudo", "-n", "journalctl", "-u", "agente-tokio", "-u", "rele-tokio", "--since", "-15min", "-p", "err",
          "--no-pager", "-q", timeout=30).splitlines()
(ok if not errs else falla)(f"errores en los últimos 15 min: {len(errs)}" + (f" · último: {errs[-1][-160:]}" if errs else ""))
kill = HOME / "KILL"
if kill.exists():
    ojo("PAUSA activa (KILL): no abre nada nuevo; " + ("la puso la PC por el relé" if "rel" in kill.read_text(
        errors="replace").lower() else "puesta a mano en el servidor"))
else:
    ok("sin pausa (KILL)")

print()
if fallas:
    print(f"HAY {len(fallas)} FALLA(S): arreglar antes de seguir.")
elif ojos:
    print(f"Todo anda; {len(ojos)} cosa(s) para mirar.")
else:
    print("Todo OK.")
sys.exit(1 if fallas else 0)
PY
