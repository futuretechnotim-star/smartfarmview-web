#!/usr/bin/env bash
# Gateway node provisioning script — idempotent, safe to re-run.
# Run as: sudo bash ~/pi-setup.sh
set -euo pipefail

DEPLOY_DIR=/opt/gateway-node
SERVICE=gateway-power

echo "[1/7] Installing system packages..."
apt-get install -y python3 python3-venv python3-pip git rsync

echo "[2/7] Creating deploy directory..."
install -d -o techno -g techno "$DEPLOY_DIR"

echo "[3/7] Setting up Python venv..."
if [ ! -f "$DEPLOY_DIR/.venv/bin/pip" ]; then
    sudo -u techno python3 -m venv "$DEPLOY_DIR/.venv"
    sudo -u techno "$DEPLOY_DIR/.venv/bin/python" -m ensurepip --upgrade
    sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install --upgrade pip -q
fi

echo "[4/7] Installing power-policy shared lib..."
# power-policy must be installed before gateway-node so the dependency resolves.
sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install -q -e "$DEPLOY_DIR/../power-policy"

echo "[5/7] Installing gateway-node..."
sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install -q -e "$DEPLOY_DIR"

echo "[6/7] Installing systemd service..."
cp "$DEPLOY_DIR/scripts/gateway-power.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"

echo "[7/7] Enabling persistent journald logging..."
# Volatile-by-default journald means a hang before this was installed leaves
# zero log history to diagnose after a manual restart — see
# docs/pico-watchdog.md and journald-gateway.conf for the incident this fixed.
install -d /etc/systemd/journald.conf.d
install -m 644 "$DEPLOY_DIR/scripts/journald-gateway.conf" \
    /etc/systemd/journald.conf.d/gateway-persistent.conf
install -d -m 2755 -o root -g systemd-journal /var/log/journal
systemctl restart systemd-journald

echo ""
echo "Setup complete. Create /opt/gateway-node/.env with credentials,"
echo "then: sudo systemctl start gateway-power"
