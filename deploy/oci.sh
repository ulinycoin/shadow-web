#!/usr/bin/env bash
# Shadow Web — minimal OCI deploy skeleton (Phase 2)
# Usage: ./deploy/oci.sh <instance-ip>
set -euo pipefail

INSTANCE_IP="${1:-}"
APP_DIR="/opt/shadow-web"
SERVICE_USER="ubuntu"

if [[ -z "$INSTANCE_IP" ]]; then
  echo "Usage: $0 <instance-public-ip>"
  exit 1
fi

echo "[1/4] Sync project to ${INSTANCE_IP}:${APP_DIR}"
rsync -avz --exclude '.env' --exclude '__pycache__' --exclude '.git' \
  ./ "${SERVICE_USER}@${INSTANCE_IP}:${APP_DIR}/"

echo "[2/4] Install dependencies on remote"
ssh "${SERVICE_USER}@${INSTANCE_IP}" bash -s <<'REMOTE'
set -euo pipefail
cd /opt/shadow-web
python3 -m pip install -e .
python3 -m playwright install chromium --with-deps || python3 -m playwright install chromium
REMOTE

echo "[3/4] Install systemd unit"
ssh "${SERVICE_USER}@${INSTANCE_IP}" bash -s <<'REMOTE'
sudo tee /etc/systemd/system/shadow-web.service >/dev/null <<'UNIT'
[Unit]
Description=Shadow Web FastAPI
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/shadow-web
EnvironmentFile=-/opt/shadow-web/.env
ExecStart=/usr/bin/python3 -m uvicorn server.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable shadow-web
sudo systemctl restart shadow-web
REMOTE

echo "[4/4] Health check"
curl -sf "http://${INSTANCE_IP}:8000/docs" >/dev/null && echo "OK: Shadow Web API live"
