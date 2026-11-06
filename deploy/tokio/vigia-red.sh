#!/usr/bin/env bash
# Vigía de la red del servidor de Tokio. Lo instala el DUEÑO (es un cambio del sistema), una sola vez:
#   sudo install -m 755 ~/trading/deploy/tokio/vigia-red.sh /usr/local/sbin/vigia-red
#   sudo install -m 644 ~/trading/deploy/tokio/vigia-red.service ~/trading/deploy/tokio/vigia-red.timer /etc/systemd/system/
#   sudo systemctl daemon-reload && sudo systemctl enable --now vigia-red.timer
# Por qué: el 5-nov-2026 a las 22:23 UTC, con el servidor sin memoria, systemd-networkd dio la interfaz por caída
# ("ens5: Could not set route: Connection timed out" y después "ens5: Failed") y dejó de renovar la IP: a los 40 min
# el servidor quedó sin red (45 min, hasta reiniciarlo). Cada minuto: si la interfaz quedó "failed" o no hay ruta por
# defecto, la reconfigura; si a los 20 s sigue sin ruta, reinicia systemd-networkd. Queda en `journalctl -t vigia-red`.
set -u
IF=$(ip -o -4 route show default | awk '{print $5; exit}')
IF=${IF:-ens5}
STATE=$(networkctl status "$IF" 2>/dev/null | awk -F': ' '/State:/ {print $2; exit}')
if ip -4 route show default | grep -q . && [[ "$STATE" != *failed* ]]; then
  exit 0
fi
logger -t vigia-red "red caída ($IF: ${STATE:-sin estado}; ruta: $(ip -4 route show default | head -1)): reconfiguro"
networkctl reconfigure "$IF" || true
sleep 20
if ! ip -4 route show default | grep -q .; then
  logger -t vigia-red "sigue sin ruta: reinicio systemd-networkd"
  systemctl restart systemd-networkd
fi
