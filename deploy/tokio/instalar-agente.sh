#!/usr/bin/env bash
# Instala o actualiza el agente de Tokio (y el relé) en el servidor. Uso, en el servidor:
#   bash ~/trading/deploy/tokio/instalar-agente.sh [rama]        (por defecto main)
# Antes, una sola vez: ~/.trading/.env con deploy/tokio/preparar_env.py (desde la PC: enviar_claves_pc.py).
set -euo pipefail
BRANCH="${1:-main}"
REPO=/home/ubuntu/trading
HOME_T=/home/ubuntu/.trading
cd "$REPO"
git fetch -q origin
OLD=$(git rev-parse HEAD 2>/dev/null || echo ninguno)
git checkout -q -B "$BRANCH" "origin/$BRANCH"
echo "código: $(git log --oneline -1)"
# Poca memoria (416 MB): pip solo si cambiaron las dependencias, y con los servicios parados (el 5-nov, pip con el
# agente arrancando hizo que el kernel lo matara por falta de memoria).
if [ "$OLD" = ninguno ] || ! git diff --quiet "$OLD" HEAD -- pyproject.toml; then
  sudo systemctl stop agente-tokio rele-tokio 2>/dev/null || true
  .venv/bin/pip install -q -e "$REPO[relay]"
fi
install -d -m 700 "$HOME_T"
if [ ! -f "$HOME_T/.env" ]; then
  echo "falta $HOME_T/.env: correr deploy/tokio/enviar_claves_pc.py desde la PC" >&2
  exit 1
fi
chmod 600 "$HOME_T/.env"
[ -f "$HOME_T/relay.key" ] || install -m 600 /home/ubuntu/relay.key "$HOME_T/relay.key"
[ -f "$HOME_T/relay.json" ] || install -m 600 deploy/tokio/relay.json "$HOME_T/relay.json"
[ -f "$HOME_T/config.yaml" ] || install -m 600 deploy/tokio/config.yaml "$HOME_T/config.yaml"
sudo install -m 644 deploy/rele-tokio/rele-tokio.service /etc/systemd/system/rele-tokio.service
sudo install -m 644 deploy/tokio/agente-tokio.service /etc/systemd/system/agente-tokio.service
sudo systemctl daemon-reload
sudo systemctl enable -q rele-tokio agente-tokio
sudo systemctl restart rele-tokio agente-tokio
sleep 8
systemctl is-active rele-tokio agente-tokio
