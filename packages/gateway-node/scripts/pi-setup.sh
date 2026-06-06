#!/usr/bin/env bash
# Gateway node provisioning script — idempotent, safe to re-run.
# Run as: sudo bash ~/pi-setup.sh
set -euo pipefail

DEPLOY_DIR=/opt/gateway-node
SERVICE=gateway-power

echo "[1/5] Installing system packages..."
apt-get install -y python3 python3-venv python3-pip git rsync

echo "[2/5] Creating deploy directory..."
install -d -o techno -g techno "$DEPLOY_DIR"

echo "[3/5] Setting up Python venv..."
if [ ! -f "$DEPLOY_DIR/.venv/bin/pip" ]; then
    sudo -u techno python3 -m venv "$DEPLOY_DIR/.venv"
    sudo -u techno "$DEPLOY_DIR/.venv/bin/python" -m ensurepip --upgrade
    sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install --upgrade pip -q
fi

echo "[4/5] Installing power-policy shared lib..."
# power-policy must be installed before gateway-node so the dependency resolves.
sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install -q -e "$DEPLOY_DIR/../power-policy"

echo "[5/5] Installing gateway-node..."
sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install -q -e "$DEPLOY_DIR"

echo "[6/6] Installing systemd service..."
cp "$DEPLOY_DIR/scripts/gateway-power.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"

echo ""
echo "Setup complete. Create /opt/gateway-node/.env with credentials,"
echo "then: sudo systemctl start gateway-power"
