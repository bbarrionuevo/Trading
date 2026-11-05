#!/usr/bin/env bash
# Instala o actualiza el relé de Tokio en el servidor (Ubuntu, usuario ubuntu). Idempotente.
# La clave del relé (/home/ubuntu/relay.key) la copia la PC por scp antes; este script no la crea ni la muestra.
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git chrony >/dev/null
sudo systemctl enable --now chrony                       # hora exacta: los datagramas aceptan 30 s de diferencia
if [ ! -d /home/ubuntu/trading/.git ]; then
  git clone -q https://github.com/bbarrionuevo/Trading.git /home/ubuntu/trading
else
  git -C /home/ubuntu/trading fetch -q origin && git -C /home/ubuntu/trading reset -q --hard origin/main
fi
python3 -m venv /home/ubuntu/trading/.venv
/home/ubuntu/trading/.venv/bin/pip install -q --upgrade pip
/home/ubuntu/trading/.venv/bin/pip install -q -e /home/ubuntu/trading
test -f /home/ubuntu/relay.key || { echo "falta /home/ubuntu/relay.key (la copia la PC)"; exit 1; }
chmod 600 /home/ubuntu/relay.key
sudo cp /home/ubuntu/trading/deploy/rele-tokio/rele-tokio.service /etc/systemd/system/rele-tokio.service
sudo systemctl daemon-reload
sudo systemctl enable --now rele-tokio
sudo systemctl restart rele-tokio
sleep 3
systemctl is-active rele-tokio
