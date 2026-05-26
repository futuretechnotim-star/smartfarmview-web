#!/usr/bin/env bash
# One-shot idempotent Pi provisioning script for SecurityMesh field nodes.
# Run via SSH from the development machine:
#   ssh techno@landplanmesh1 'bash -s' < packages/field-node/scripts/pi-setup.sh
# Or directly on the Pi after cloning.

set -euo pipefail

DEPLOY_PATH="/opt/field-node"
VENV_PATH="$DEPLOY_PATH/.venv"
CAPTURE_DIR="$DEPLOY_PATH/captures"

echo "==> Phase 1: OS prerequisites"
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-venv python3-pip \
    python3-picamera2 \
    rpicam-apps \
    git \
    rsync

echo "==> Phase 2: User groups (video for camera access)"
sudo usermod -aG video techno || true

echo "==> Phase 3: Create deploy and capture directories"
sudo mkdir -p "$DEPLOY_PATH" "$CAPTURE_DIR"
sudo chown -R techno:techno "$DEPLOY_PATH"

echo "==> Phase 4: Create Python venv"
# --system-site-packages gives the venv access to picamera2 and its C extensions
# which are installed as system packages and cannot be pip-installed cleanly on Pi OS.
if [ ! -d "$VENV_PATH" ]; then
    python3 -m venv --system-site-packages "$VENV_PATH"
    "$VENV_PATH/bin/pip" install --upgrade pip --quiet
    echo "  venv ready"
    echo "  venv created at $VENV_PATH"
else
    echo "  venv already exists"
fi

echo "==> Phase 5: Install systemd service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="$SCRIPT_DIR/field-node.service"
if [ -f "$SERVICE_SRC" ]; then
    sudo cp "$SERVICE_SRC" /etc/systemd/system/field-node.service
    sudo systemctl daemon-reload
    echo "  systemd service installed (not enabled yet — run after first code deploy)"
else
    echo "  WARNING: $SERVICE_SRC not found, skipping service install"
fi

echo "==> Phase 6: Verify camera"
echo "  Running rpicam-hello --list-cameras:"
rpicam-hello --list-cameras || echo "  WARNING: camera not detected — check cable and config.txt"

echo ""
echo "==> Setup complete!"
echo "  Next steps:"
echo "  1. Add GitHub Actions secrets: PI_SSH_KEY, PI_SSH_KNOWN_HOST, TAILSCALE_OAUTH_CLIENT_ID, TAILSCALE_OAUTH_SECRET"
echo "  2. Push to main: git push origin main  (triggers deploy workflow)"
echo "  3. Enable service: ssh techno@landplanmesh1 'sudo systemctl enable --now field-node'"
echo "  4. Check logs: ssh techno@landplanmesh1 'journalctl -u field-node -f'"
