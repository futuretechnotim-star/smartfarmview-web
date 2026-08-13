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
echo "==> Phase 11: wlan1 sfv-fieldmesh uplink (BrosTrend AC5L, optional)"
# ---------------------------------------------------------------------------
# This fleet-wide script must stay a no-op on every field node that doesn't
# have the adapter — gate entirely on the hardware actually being present
# (rtw88_8821cu, in-kernel since 6.12) rather than any per-node config flag.
#
# Originally this phase turned wlan1 into a range-extension AP (relaying
# sfv-fieldmesh outward as sfv-fieldmesh-ext1, NAT'd through wlan0). That was
# retired 2026-08-12: `iw phy info` on this driver reports "interface
# combinations are not supported", so the adapter can be a client OR an AP,
# never both concurrently — a real repeater isn't possible on this hardware.
# The onboard wlan0 radio (weak, short range) was the wrong place to spend
# the good external-antenna radio anyway, so wlan1 now carries the node's own
# uplink to sfv-fieldmesh instead. wlan0 is left alone as a second, lower-
# priority path (e.g. WebsterFiber during bench testing) — see
# [[project_landplanmesh1_wifi_fallback]] for the autoconnect-priority
# pattern this mirrors.
if [ -e /sys/class/net/wlan1/device/uevent ] && grep -q "8821cu" /sys/class/net/wlan1/device/uevent; then
    echo "  AC5L (wlan1) detected — configuring as sfv-fieldmesh client uplink"

    # Tear down any range-extension AP config left over from a node that was
    # provisioned before 2026-08-12. All of this is idempotent/safe to run
    # on a node that never had it either.
    sudo systemctl disable --now sfv-ap-ext hostapd dnsmasq 2>/dev/null || true
    sudo rm -f /etc/systemd/system/sfv-ap-ext.service \
               /etc/dnsmasq.d/sfv-fieldmesh-ext1.conf \
               /etc/sysctl.d/90-sfv-forward.conf \
               /etc/NetworkManager/conf.d/unmanaged-wlan1.conf
    sudo iptables -t nat -D POSTROUTING -s 192.168.51.0/24 -o wlan0 -j MASQUERADE 2>/dev/null || true
    sudo iptables -D FORWARD -i wlan1 -o wlan0 -j ACCEPT 2>/dev/null || true
    command -v netfilter-persistent >/dev/null 2>&1 && sudo netfilter-persistent save || true
    sudo systemctl daemon-reload

    # Let NetworkManager manage wlan1 like any other WiFi device. A plain
    # `nmcli device set managed yes` isn't enough to undo the old
    # unmanaged-wlan1.conf — NM keeps that device's unmanaged state in memory
    # from its last start/reload, so removing the conf.d file needs an actual
    # restart to take effect.
    sudo systemctl restart NetworkManager
    sleep 3
    sudo nmcli device set wlan1 managed yes 2>/dev/null || true

    # Reuse the sfv-fieldmesh password already staged on-device for wlan0
    # (/boot/firmware/network-config, not committed to git) rather than
    # hardcoding a secret in this script.
    FIELDMESH_PSK=$(python3 -c "
import yaml
try:
    with open('/boot/firmware/network-config') as f:
        cfg = yaml.safe_load(f) or {}
    print(cfg.get('network', {}).get('wifis', {}).get('wlan0', {})
             .get('access-points', {}).get('sfv-fieldmesh', {})
             .get('password', ''))
except FileNotFoundError:
    pass
" 2>/dev/null)

    if [ -z "$FIELDMESH_PSK" ]; then
        echo "  WARNING: no sfv-fieldmesh password found in /boot/firmware/network-config — skipping wlan1 profile"
    else
        CONN_NAME="netplan-wlan1-sfv-fieldmesh"
        if nmcli -t -f NAME connection show | grep -qx "$CONN_NAME"; then
            sudo nmcli connection modify "$CONN_NAME" wifi-sec.psk "$FIELDMESH_PSK"
        else
            sudo nmcli connection add type wifi ifname wlan1 con-name "$CONN_NAME" \
                ssid sfv-fieldmesh \
                wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$FIELDMESH_PSK" \
                connection.autoconnect yes connection.autoconnect-priority 10
        fi
        echo "  wlan1 configured as sfv-fieldmesh client (autoconnect-priority 10)"

        # The cloud-init/netplan-rendered wlan0 profile for the same SSID
        # (from /boot/firmware/network-config) has no connection.interface-name
        # pin, so NetworkManager is free to hand it to *either* radio — and
        # will happily grab wlan1 for it, leaving wlan0 idle with nothing to
        # connect to and no real redundancy. Pin it to wlan0 explicitly so
        # both radios independently hold their own connection to sfv-fieldmesh.
        WLAN0_CONN=$(nmcli -t -f NAME,DEVICE connection show \
            | awk -F: '$1 ~ /^netplan-wlan0-sfv-fieldmesh$/ {print $1; exit}')
        if [ -n "$WLAN0_CONN" ]; then
            sudo nmcli connection modify "$WLAN0_CONN" connection.interface-name wlan0
            # wlan1 give it a better (lower) route metric than wlan0 — same
            # destination network, but the AC5L is the better radio and
            # should win the default route whenever both are connected.
            sudo nmcli connection modify "$CONN_NAME" ipv4.route-metric 100
            sudo nmcli connection modify "$WLAN0_CONN" ipv4.route-metric 200
            # Route-metric changes only take effect on (re)activation, not on
            # an already-connected profile — reassert both explicitly.
            sudo nmcli connection up "$CONN_NAME" ifname wlan1 2>/dev/null || true
            sudo nmcli connection up "$WLAN0_CONN" ifname wlan0 2>/dev/null || true
            echo "  wlan0's sfv-fieldmesh profile pinned to wlan0 (was unpinned/stealable by wlan1)"
        fi
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
