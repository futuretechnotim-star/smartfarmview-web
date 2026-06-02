#!/usr/bin/env bash
# Idempotent setup of the HA `media` Samba mount + detection-image store path.
#
# Called by pi-setup.sh (Phase 9). Split into its own script so the idempotency
# contract — fstab not duplicated, a pre-existing FIELD_NODE_DETECTION_STORE_DIR
# not overwritten — is unit-testable without root or cifs. See
# tests/unit/test_pi_setup.py.
#
# Overridable for tests / non-root runs:
#   FSTAB, ENV_FILE, CREDS_FILE, MNT_BASE  file locations (default to real paths)
#   SUDO        command prefix for privileged writes (default "sudo"; tests set "")
#   SKIP_PRIV   "1" skips apt/mkdir/chmod/mount (tests set "1")
#   HOSTN       node hostname (default $(hostname))
#
# NOTE ON PATHS: we mount the `media` share ROOT at /mnt/ha-media and store
# images at /mnt/ha-media/landplan/<host>. That lands files at HA's
# config/media/landplan/<host>/{id}.jpg, which HA serves at
# /media/local/landplan/<host>/{id}.jpg — matching the LandPlan API image proxy.
# (Shared team convention; the Pi creates the landplan/<host> subdir.)
set -euo pipefail

FSTAB="${FSTAB:-/etc/fstab}"
ENV_FILE="${ENV_FILE:-/opt/field-node/.env}"
CREDS_FILE="${CREDS_FILE:-/etc/ha-samba.creds}"
MNT_BASE="${MNT_BASE:-/mnt/ha-media}"
SUDO="${SUDO-sudo}"
SKIP_PRIV="${SKIP_PRIV:-0}"
HOSTN="${HOSTN:-$(hostname)}"
SMB_USER="${SMB_USER:-gateway-node}"
SMB_PASS="${SMB_PASS:-gateway-node}"

# Mount the `media` share root directly at /mnt/ha-media (shared mountpoint);
# each node writes only under its own landplan/<host> subdir.
MOUNT_POINT="$MNT_BASE"
STORE_DIR="$MOUNT_POINT/landplan/$HOSTN"

# --- cifs-utils + credentials file + mount point (privileged) --------------
if [ "$SKIP_PRIV" != "1" ]; then
    if ! dpkg-query -W -f='${Status}' cifs-utils 2>/dev/null | grep -q "install ok installed"; then
        $SUDO apt-get update -qq && $SUDO apt-get install -y cifs-utils
    fi
    if [ ! -f "$CREDS_FILE" ]; then
        printf 'username=%s\npassword=%s\n' "$SMB_USER" "$SMB_PASS" | $SUDO tee "$CREDS_FILE" >/dev/null
        $SUDO chmod 600 "$CREDS_FILE"
        echo "  wrote $CREDS_FILE (chmod 600)"
    fi
    $SUDO mkdir -p "$MOUNT_POINT"
fi

# --- read HA host from env; skip the mount silently if unset ---------------
HA_SMB_HOST=""
if [ -f "$ENV_FILE" ]; then
    HA_SMB_HOST="$(grep -E '^FIELD_NODE_HA_SMB_HOST=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
fi
if [ -z "$HA_SMB_HOST" ]; then
    echo "  FIELD_NODE_HA_SMB_HOST unset — skipping HA media mount (image storage stays disabled)"
    exit 0
fi

# --- fstab entry (idempotent; matched on the mount point) ------------------
FSTAB_LINE="//${HA_SMB_HOST}/media  ${MOUNT_POINT}  cifs  credentials=${CREDS_FILE},uid=techno,gid=techno,iocharset=utf8,_netdev,nofail  0  0"
if grep -qF " ${MOUNT_POINT} " "$FSTAB" 2>/dev/null; then
    echo "  fstab entry already present for ${MOUNT_POINT}"
else
    echo "$FSTAB_LINE" | $SUDO tee -a "$FSTAB" >/dev/null
    echo "  added fstab entry for ${MOUNT_POINT}"
fi

# --- mount now (privileged); nofail keeps boot safe if HA is unreachable ----
if [ "$SKIP_PRIV" != "1" ]; then
    $SUDO systemctl daemon-reload || true
    $SUDO mount "$MOUNT_POINT" 2>/dev/null \
        || echo "  NOTE: mount deferred (HA unreachable now; _netdev/nofail mounts it on boot)"
fi

# --- set FIELD_NODE_DETECTION_STORE_DIR if not already present -------------
if [ -f "$ENV_FILE" ] && grep -qE '^FIELD_NODE_DETECTION_STORE_DIR=' "$ENV_FILE"; then
    echo "  FIELD_NODE_DETECTION_STORE_DIR already set — leaving as-is"
else
    echo "FIELD_NODE_DETECTION_STORE_DIR=${STORE_DIR}" | $SUDO tee -a "$ENV_FILE" >/dev/null
    echo "  set FIELD_NODE_DETECTION_STORE_DIR=${STORE_DIR}"
fi
