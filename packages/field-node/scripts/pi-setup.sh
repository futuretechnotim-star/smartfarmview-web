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
# Shared power-policy lib lives outside $DEPLOY_PATH (it is not field-node-specific).
# Must exist + be techno-owned before the deploy rsyncs into it (/opt is root-owned).
POLICY_PATH="/opt/power-policy"

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
I2C_CHANGED=0
if [ ! -e /dev/i2c-1 ]; then
    sudo raspi-config nonint do_i2c 0
    I2C_CHANGED=1
    echo "  I2C enabled — reboot required to activate /dev/i2c-1"
else
    echo "  I2C already active (/dev/i2c-1 present)"
fi

# ---------------------------------------------------------------------------
echo "==> Phase 3: User groups (video + i2c)"
# ---------------------------------------------------------------------------
sudo usermod -aG video,i2c techno || true

# ---------------------------------------------------------------------------
echo "==> Phase 4: Create deploy, capture, and shared-lib directories"
# ---------------------------------------------------------------------------
sudo mkdir -p "$DEPLOY_PATH" "$CAPTURE_DIR" "$POLICY_PATH"
sudo chown -R techno:techno "$DEPLOY_PATH" "$POLICY_PATH"

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
# Remote reboot (e.g. after pi-setup.sh enables I2C or camera overlay)
techno ALL=(ALL) NOPASSWD: /usr/sbin/reboot
EOF
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" && echo "  sudoers rules installed/updated" \
    || { echo "  ERROR: sudoers syntax check failed — file not applied"; exit 1; }

# ---------------------------------------------------------------------------
echo "==> Phase 8: Camera sensor overlay (Arducam IMX519) + verify"
# ---------------------------------------------------------------------------
# The IMX519 is NOT auto-detected by libcamera (unlike the old OV5647) — it needs
# an explicit device-tree overlay. This block is idempotent and only sets
# CAMERA_CHANGED (→ one-time reboot at the end) when it actually edits config.txt.
# If the overlay is absent from this OS image, install Arducam's kernel driver:
#   https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver  (-p imx519_kernel_driver)
CONFIG_TXT="/boot/firmware/config.txt"
[ -f "$CONFIG_TXT" ] || CONFIG_TXT="/boot/config.txt"
CAMERA_OVERLAY="imx519"
CAMERA_CHANGED=0

if [ -f "$CONFIG_TXT" ]; then
    # Auto-detect conflicts with a manual sensor overlay — turn it off.
    if grep -qE '^camera_auto_detect=1' "$CONFIG_TXT"; then
        sudo sed -i 's/^camera_auto_detect=1/camera_auto_detect=0/' "$CONFIG_TXT"
        CAMERA_CHANGED=1; echo "  set camera_auto_detect=0"
    elif ! grep -qE '^camera_auto_detect=' "$CONFIG_TXT"; then
        echo 'camera_auto_detect=0' | sudo tee -a "$CONFIG_TXT" >/dev/null
        CAMERA_CHANGED=1; echo "  added camera_auto_detect=0"
    fi
    if ! grep -qE "^dtoverlay=${CAMERA_OVERLAY}([,[:space:]]|$)" "$CONFIG_TXT"; then
        echo "dtoverlay=${CAMERA_OVERLAY}" | sudo tee -a "$CONFIG_TXT" >/dev/null
        CAMERA_CHANGED=1; echo "  added dtoverlay=${CAMERA_OVERLAY}"
    fi
    [ "$CAMERA_CHANGED" -eq 0 ] && echo "  camera overlay already configured"
else
    echo "  WARNING: config.txt not found — cannot configure camera overlay"
fi

# Verify (only meaningful once the overlay is active, i.e. after the reboot below).
# Capture to a var first — piping rpicam into head triggers SIGPIPE under
# `pipefail` and would emit a false "not detected" warning.
CAM_OUT="$(rpicam-hello --list-cameras 2>&1 || true)"
echo "$CAM_OUT" | head -8
if echo "$CAM_OUT" | grep -q "$CAMERA_OVERLAY"; then
    echo "  camera detected ($CAMERA_OVERLAY)"
else
    echo "  NOTE: $CAMERA_OVERLAY not detected yet — expected before the post-overlay reboot"
fi

# ---------------------------------------------------------------------------
echo "==> Phase 9: HA media Samba mount (detection image store)"
# ---------------------------------------------------------------------------
# Mounts HA's `media` share and sets FIELD_NODE_DETECTION_STORE_DIR. Idempotent
# and graceful: if FIELD_NODE_HA_SMB_HOST is unset in .env, it skips silently and
# image storage stays disabled. Logic lives in setup-ha-mount.sh (unit-tested).
HA_MOUNT_SCRIPT="$DEPLOY_PATH/scripts/setup-ha-mount.sh"
if [ -f "$HA_MOUNT_SCRIPT" ]; then
    bash "$HA_MOUNT_SCRIPT" || echo "  WARNING: HA mount setup returned non-zero (continuing)"
else
    echo "  setup-ha-mount.sh not deployed yet (first-time stdin run) — runs on next deploy"
fi

# ---------------------------------------------------------------------------
echo "==> Phase 10: Object detection model (COCO SSD MobileNet V1 INT8)"
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

# ---------------------------------------------------------------------------
echo "==> Phase 11: WiFi range-extension AP (BrosTrend AC5L, optional)"
# ---------------------------------------------------------------------------
# This fleet-wide script must stay a no-op on every field node that doesn't
# have the adapter — gate entirely on the hardware actually being present
# (rtw88_8821cu, in-kernel since 6.12) rather than any per-node config flag.
# Distinct SSID/subnet from the gateway's own sfv-fieldmesh AP (192.168.50.0/24)
# so overlapping radio coverage can't cause duplicate-IP/rogue-DHCP conflicts —
# this is a separate NAT island relayed back out through this node's own wlan0.
if [ -e /sys/class/net/wlan1/device/uevent ] && grep -q "8821cu" /sys/class/net/wlan1/device/uevent; then
    echo "  AC5L (wlan1) detected — configuring range-extension AP"

    WIFI_EXT_PACKAGES=(hostapd dnsmasq iptables-persistent)
    WIFI_EXT_NEEDED=()
    for pkg in "${WIFI_EXT_PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null \
            | grep -q "install ok installed" || WIFI_EXT_NEEDED+=("$pkg")
    done
    if [ ${#WIFI_EXT_NEEDED[@]} -gt 0 ]; then
        sudo apt-get update -qq
        sudo apt-get install -y "${WIFI_EXT_NEEDED[@]}"
    fi

    sudo tee /etc/NetworkManager/conf.d/unmanaged-wlan1.conf > /dev/null << 'EOF'
[keyfile]
unmanaged-devices=interface-name:wlan1
EOF
    # Writing the conf.d file alone only takes effect on NetworkManager's next
    # start/reload — if wlan1 was hot-plugged into an already-running system
    # (as opposed to being present at boot), NM can grab it as a second WiFi
    # *client* first (using whatever saved profile wlan0 already has, e.g.
    # the gateway's own sfv-fieldmesh AP) before this phase ever runs, and
    # hostapd/dnsmasq being "active" doesn't mean they actually hold the
    # interface. Release it from NM immediately, at runtime, every run.
    sudo nmcli device set wlan1 managed no 2>/dev/null || true

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/stdin}")" 2>/dev/null && pwd || echo "")"
    CONF_SRC_DIR="$DEPLOY_PATH/scripts"
    [ -f "$CONF_SRC_DIR/wifi-ext-hostapd.conf" ] || CONF_SRC_DIR="$SCRIPT_DIR"

    if [ -f "$CONF_SRC_DIR/wifi-ext-hostapd.conf" ]; then
        sudo cp "$CONF_SRC_DIR/wifi-ext-hostapd.conf" /etc/hostapd/hostapd.conf
        sudo cp "$CONF_SRC_DIR/wifi-ext-dnsmasq.conf" /etc/dnsmasq.d/sfv-fieldmesh-ext1.conf
        sudo cp "$CONF_SRC_DIR/sfv-ap-ext.service" /etc/systemd/system/sfv-ap-ext.service

        echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/90-sfv-forward.conf > /dev/null
        sudo sysctl -p /etc/sysctl.d/90-sfv-forward.conf > /dev/null

        # Idempotent: only add the NAT/forward rules if not already present.
        sudo iptables -t nat -C POSTROUTING -s 192.168.51.0/24 -o wlan0 -j MASQUERADE 2>/dev/null \
            || sudo iptables -t nat -A POSTROUTING -s 192.168.51.0/24 -o wlan0 -j MASQUERADE
        sudo iptables -C FORWARD -i wlan1 -o wlan0 -j ACCEPT 2>/dev/null \
            || sudo iptables -A FORWARD -i wlan1 -o wlan0 -j ACCEPT
        sudo iptables -C FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
            || sudo iptables -A FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
        sudo netfilter-persistent save

        sudo systemctl daemon-reload
        sudo systemctl unmask hostapd
        sudo systemctl enable sfv-ap-ext hostapd dnsmasq
        # Restart (not just enable --now) so an already-"active" unit that
        # never actually held the interface (see NM race above) reclaims it.
        sudo systemctl restart sfv-ap-ext
        sudo systemctl restart hostapd
        sudo systemctl restart dnsmasq
        echo "  range-extension AP configured (ssid=sfv-fieldmesh-ext1, 192.168.51.0/24)"
    else
        echo "  WARNING: wifi-ext-*.conf not deployed yet — skipping (will apply on next deploy)"
    fi
else
    echo "  no AC5L detected on wlan1 — skipping (expected on nodes without the adapter)"
fi

echo ""
echo "==> Setup complete!"
echo "  Node: $(hostname)"
echo ""
echo "  First-time next steps (skip if this is a re-run):"
echo "  1. Reboot to activate I2C:  sudo reboot"
echo "  2. Add node to infra/nodes.json and push — all future updates are automatic"

# ---------------------------------------------------------------------------
# Reboot ONLY when the camera overlay changed this run (one-time, when the
# sensor overlay is first introduced/altered). Steady-state re-runs make no
# change and never reboot — so the GitHub Actions deploy is unaffected once the
# overlay is in place. A loaded sensor overlay requires a reboot to take effect.
# ---------------------------------------------------------------------------
if [ "${CAMERA_CHANGED:-0}" -eq 1 ] || [ "${I2C_CHANGED:-0}" -eq 1 ]; then
    echo ""
    REASONS=()
    [ "${CAMERA_CHANGED:-0}" -eq 1 ] && REASONS+=("camera overlay")
    [ "${I2C_CHANGED:-0}" -eq 1 ] && REASONS+=("I2C")
    REASON_STR=$(IFS=", "; echo "${REASONS[*]}")
    echo "==> ${REASON_STR} changed — rebooting now to activate hardware."
    sudo reboot
fi
