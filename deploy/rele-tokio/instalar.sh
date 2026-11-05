#!/usr/bin/env bash
# Instala o actualiza el relé de Tokio en el servidor (Ubuntu 22.04, usuario ubuntu). Idempotente.
# Uso: bash instalar.sh [rama]   (por defecto main)
# La clave del relé (/home/ubuntu/relay.key) la copia la PC por scp antes; este script no la crea ni la muestra.
set -euo pipefail
sudo apt-get update -qq
BRANCH="${1:-main}"
sudo apt-get install -y -qq python3.11 python3.11-venv git >/dev/null    # el sistema pide Python >= 3.11
timedatectl show -p NTPSynchronized --value | grep -q yes || echo "OJO: la hora no está sincronizada (30 s)"
if [ ! -d /home/ubuntu/trading/.git ]; then
  git clone -q https://github.com/bbarrionuevo/Trading.git /home/ubuntu/trading
else
  git -C /home/ubuntu/trading fetch -q origin
fi
git -C /home/ubuntu/trading checkout -q -B "$BRANCH" "origin/$BRANCH"
python3.11 -m venv /home/ubuntu/trading/.venv
/home/ubuntu/trading/.venv/bin/pip install -q --upgrade pip
/home/ubuntu/trading/.venv/bin/pip install -q -e "/home/ubuntu/trading[relay]"    # con Telethon
test -f /home/ubuntu/relay.key || { echo "falta /home/ubuntu/relay.key (la copia la PC)"; exit 1; }
chmod 600 /home/ubuntu/relay.key
sudo cp /home/ubuntu/trading/deploy/rele-tokio/rele-tokio.service /etc/systemd/system/rele-tokio.service
sudo systemctl daemon-reload
sudo systemctl enable --now rele-tokio
sudo systemctl restart rele-tokio
sleep 3
systemctl is-active rele-tokio
