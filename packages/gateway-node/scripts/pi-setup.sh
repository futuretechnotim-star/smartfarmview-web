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
    # --system-site-packages: picamera2 and adafruit_servokit are only
    # available via apt (python3-picamera2, python3-adafruit-circuitpython-*),
    # not PyPI-installable in an isolated venv on this platform.
    sudo -u techno python3 -m venv --system-site-packages "$DEPLOY_DIR/.venv"
    sudo -u techno "$DEPLOY_DIR/.venv/bin/python" -m ensurepip --upgrade
    sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install --upgrade pip -q
fi

echo "[4/7] Installing power-policy shared lib..."
# power-policy must be installed before gateway-node so the dependency resolves.
sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install -q -e "$DEPLOY_DIR/../power-policy"

echo "[5/7] Installing gateway-node..."
sudo -u techno "$DEPLOY_DIR/.venv/bin/pip" install -q -e "$DEPLOY_DIR[hardware]"

echo "[6/7] Installing systemd service..."
cp "$DEPLOY_DIR/scripts/gateway-power.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"

echo "[7/7] Enabling persistent journald logging..."
# Volatile-by-default journald means a hang before this was installed leaves
# zero log history to diagnose after a manual restart — see
# docs/pico-watchdog.md and zz-gateway-persistent.conf for the incident this
# fixed. Filename prefixed zz- so it sorts (and applies) after Raspberry Pi
# OS's own 40-rpi-volatile-storage.conf drop-in, which otherwise wins.
install -d /etc/systemd/journald.conf.d
install -m 644 "$DEPLOY_DIR/scripts/zz-gateway-persistent.conf" \
    /etc/systemd/journald.conf.d/zz-gateway-persistent.conf
install -d -m 2755 -o root -g systemd-journal /var/log/journal
systemctl restart systemd-journald
# NOTE (2026-07-31): confirmed the effective merged config does show
# Storage=persistent after this, but the running journald kept writing to
# /run/log/journal (volatile) regardless — even restarting after setting
# Storage=persistent directly in the main journald.conf had no effect. Not
# yet understood; a full reboot may be required for the storage transition
# to actually take on this image. Re-verify after the next reboot.

echo ""
echo "Setup complete. Create /opt/gateway-node/.env with credentials,"
echo "then: sudo systemctl start gateway-power"
