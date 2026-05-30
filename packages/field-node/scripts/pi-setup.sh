#!/usr/bin/env bash
# Idempotent provisioning script for SecurityMesh field nodes.
#
# First-time setup (manual, once per new node):
#   ssh techno@<ip> 'bash -s' < packages/field-node/scripts/pi-setup.sh
#
# All subsequent runs are automatic — triggered by the GitHub Actions deploy
# workflow on every push to main. Adding a new apt package or sudoers rule
# here takes effect on the next push without manual SSH.

set -euo pipefail

DEPLOY_PATH="/opt/field-node"
VENV_PATH="$DEPLOY_PATH/.venv"
CAPTURE_DIR="$DEPLOY_PATH/captures"

# ---------------------------------------------------------------------------
echo "==> Phase 1: OS prerequisites"
# ---------------------------------------------------------------------------
# Check which packages are missing before calling apt — makes re-runs fast.
PACKAGES=(
    python3 python3-venv python3-pip
    python3-picamera2
    rpicam-apps
    git rsync
    wireless-tools
    python3-lgpio python3-gpiozero
)

NEEDED=()
for pkg in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null \
        | grep -q "install ok installed" || NEEDED+=("$pkg")
done

if [ ${#NEEDED[@]} -gt 0 ]; then
    echo "  Installing: ${NEEDED[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${NEEDED[@]}"
else
    echo "  All packages already installed — skipping apt"
fi

# ---------------------------------------------------------------------------
echo "==> Phase 2: Enable I2C (required for INA219 power monitor)"
# ---------------------------------------------------------------------------
sudo raspi-config nonint do_i2c 0
echo "  I2C enabled (reboot required before /dev/i2c-1 appears on a fresh install)"

# ---------------------------------------------------------------------------
echo "==> Phase 3: User groups (video + i2c)"
# ---------------------------------------------------------------------------
sudo usermod -aG video,i2c techno || true

# ---------------------------------------------------------------------------
echo "==> Phase 4: Create deploy and capture directories"
# ---------------------------------------------------------------------------
sudo mkdir -p "$DEPLOY_PATH" "$CAPTURE_DIR"
sudo chown -R techno:techno "$DEPLOY_PATH"

# ---------------------------------------------------------------------------
echo "==> Phase 5: Create Python venv"
# ---------------------------------------------------------------------------
# --system-site-packages exposes picamera2 and lgpio (C extensions installed
# as system packages that cannot be cleanly pip-installed on Pi OS).
if [ ! -d "$VENV_PATH" ]; then
    python3 -m venv --system-site-packages "$VENV_PATH"
    "$VENV_PATH/bin/pip" install --upgrade pip --quiet
    echo "  venv created at $VENV_PATH"
else
    echo "  venv already exists"
fi

# ---------------------------------------------------------------------------
echo "==> Phase 6: Install systemd service"
# ---------------------------------------------------------------------------
# Prefer the deployed copy (set after first rsync); fall back to relative path
# when running manually from a repo checkout before first deploy.
SERVICE_SRC="$DEPLOY_PATH/scripts/field-node.service"
if [ ! -f "$SERVICE_SRC" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/stdin}")" 2>/dev/null && pwd || echo "")"
    SERVICE_SRC="$SCRIPT_DIR/field-node.service"
fi

if [ -f "$SERVICE_SRC" ]; then
    sudo cp "$SERVICE_SRC" /etc/systemd/system/field-node.service
    sudo systemctl daemon-reload
    echo "  systemd service installed"
else
    echo "  WARNING: service file not found — skipping (will install on first deploy)"
fi

# ---------------------------------------------------------------------------
echo "==> Phase 7: Passwordless sudo rules"
# ---------------------------------------------------------------------------
# Always overwrite so re-running this script picks up rule changes.
# NOTE: pi-setup.sh itself lives under $DEPLOY_PATH which is owned by techno.
# The NOPASSWD rule for this script is a pragmatic tradeoff for a
# single-operator trusted device fleet — acceptable for this use case.
SUDOERS_FILE="/etc/sudoers.d/field-node"
sudo tee "$SUDOERS_FILE" > /dev/null << 'EOF'
# Automated deploy (GitHub Actions)
techno ALL=(ALL) NOPASSWD: /bin/bash /opt/field-node/scripts/pi-setup.sh
# Service management
techno ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart field-node
techno ALL=(ALL) NOPASSWD: /usr/bin/systemctl start field-node
techno ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop field-node
techno ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable field-node
# Power management: CPU governor (Pi Zero 2 W has 4 cores)
techno ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
techno ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/devices/system/cpu/cpu1/cpufreq/scaling_governor
techno ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor
techno ALL=(ALL) NOPASSWD: /usr/bin/tee /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor
# Power management: WiFi PSM
techno ALL=(ALL) NOPASSWD: /usr/sbin/iwconfig wlan0 power on
techno ALL=(ALL) NOPASSWD: /usr/sbin/iwconfig wlan0 power off
EOF
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" && echo "  sudoers rules installed/updated" \
    || { echo "  ERROR: sudoers syntax check failed — file not applied"; exit 1; }

# ---------------------------------------------------------------------------
echo "==> Phase 8: Verify camera"
# ---------------------------------------------------------------------------
rpicam-hello --list-cameras 2>&1 || echo "  WARNING: camera not detected — check cable and config.txt"

# ---------------------------------------------------------------------------
echo "==> Phase 9: Object detection model (COCO SSD MobileNet V1 INT8)"
# ---------------------------------------------------------------------------
MODEL_DIR="$DEPLOY_PATH/models"
MODEL_FILE="$MODEL_DIR/detect.tflite"
LABELS_FILE="$MODEL_DIR/labelmap.txt"
MODEL_URL="https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip"

mkdir -p "$MODEL_DIR"

if [ ! -f "$MODEL_FILE" ] || [ ! -f "$LABELS_FILE" ]; then
    echo "  Downloading COCO SSD MobileNet V1 model (~4 MB)..."
    wget -q "$MODEL_URL" -O /tmp/field_node_model.zip
    unzip -qo /tmp/field_node_model.zip detect.tflite labelmap.txt -d "$MODEL_DIR"
    rm -f /tmp/field_node_model.zip
    echo "  Model downloaded to $MODEL_DIR"
else
    echo "  Model already present — skipping download"
fi

echo ""
echo "==> Setup complete!"
echo "  Node: $(hostname)"
echo ""
echo "  First-time next steps (skip if this is a re-run):"
echo "  1. Reboot to activate I2C:  sudo reboot"
echo "  2. Add node to infra/nodes.json and push — all future updates are automatic"
