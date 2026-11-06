#!/usr/bin/env bash
# Dónde está la memoria del agente de Tokio y cuánto se frena su bucle (solo lee /proc y el latido; no toca nada).
# Uso, en el servidor: bash ~/trading/deploy/tokio/memoria.sh
#   arena principal = la memoria de glibc del hilo principal ([heap])
#   arenas de hilos = las de glibc de los demás hilos (con MALLOC_ARENA_MAX=2, desde el 6-nov, como mucho 1)
#   resto           = la memoria propia de Python (arenas de 1 MiB) y los bloques grandes
set -euo pipefail
PID=$(systemctl show -p MainPID --value agente-tokio)
if [ -z "$PID" ] || [ "$PID" = 0 ]; then
  echo "el agente no está corriendo"
  exit 1
fi
DESDE=$(systemctl show -p ActiveEnterTimestamp --value agente-tokio)
HORAS=$(awk -v a="$(date -d "$DESDE" +%s)" -v b="$(date +%s)" 'BEGIN { printf "%.1f", (b - a) / 3600 }')
CUR=$(( $(systemctl show -p MemoryCurrent --value agente-tokio) / 1048576 ))
echo "agente: encendido hace $HORAS h · memoria del servicio $CUR MB (MemoryHigh 400, MemoryMax 500)"
awk '
  /^[0-9a-f]+-[0-9a-f]+ / {
    split($1, a, "-"); st = strtonum("0x" a[1]); en = strtonum("0x" a[2])
    anon = (NF < 6 || $6 == "[heap]"); isheap = ($6 == "[heap]"); perm = $2
    if (pperm == "rw-p" && panon && !pheap && perm == "---p" && anon && st == pend && (en - st) > 1048576) {
      hilos += prss; n++; resto -= prss
    }
    pend = en; pperm = perm; panon = anon; pheap = isheap; cur_anon = anon; cur_heap = isheap
    next
  }
  /^Rss:/ { prss = $2; if (cur_heap) heap += $2; else if (cur_anon && pperm == "rw-p") resto += $2 }
  END { printf "  arena principal %d MB · arenas de hilos %d MB (%d) · resto (Python) %d MB\n", heap / 1024, hilos / 1024, n, resto / 1024 }
' "/proc/$PID/smaps"
python3 -c '
import json
lag = json.load(open("/home/ubuntu/.trading/state/heartbeat.json")).get("loop_lag") or {}
print("  bucle en el último minuto: mediana %s ms, p99 %s ms, máximo %s ms"
      % (lag.get("p50_ms"), lag.get("p99_ms"), lag.get("max_ms")))
'
