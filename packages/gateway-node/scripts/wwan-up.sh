#!/bin/bash
# Bring up LTE data connection on wwan0 via QMI.
# Called by wwan0.service on boot. ModemManager must be masked — direct QMI
# access conflicts with MM holding the device.
set -e

DEV=/dev/cdc-wdm0
IFACE=wwan0
AT_PORT=/dev/ttyUSB_at

# raw_ip=Y is required for QMI bearer IP assignment (non-Ethernet framing).
# Belt-and-suspenders: wwan0-raw-ip.service sets this via udev, but we set it
# here too in case the service races against our own startup.
if [ -f "/sys/class/net/${IFACE}/qmi/raw_ip" ]; then
    echo Y > "/sys/class/net/${IFACE}/qmi/raw_ip"
fi

# Clean up any lingering QMI session state from a previous (failed) run.
qmi-network "$DEV" stop 2>/dev/null || true

# Start QMI data session
qmi-network "$DEV" start

# Get IP settings from active bearer
SETTINGS=$(qmicli -p -d "$DEV" --wds-get-current-settings 2>/dev/null)
ADDR=$(echo "$SETTINGS" | awk '/IPv4 address:/ {print $NF}')
MASK=$(echo "$SETTINGS" | awk '/IPv4 subnet mask:/ {print $NF}')
GW=$(echo "$SETTINGS" | awk '/IPv4 gateway address:/ {print $NF}')
DNS=$(echo "$SETTINGS" | awk '/IPv4 primary DNS:/ {print $NF}')

# Convert subnet mask to prefix length
prefix_len() {
  local mask=$1
  local bits=0
  IFS='.' read -r -a octets <<< "$mask"
  for oct in "${octets[@]}"; do
    local b=$oct
    while [ $b -gt 0 ]; do
      bits=$((bits + (b & 1)))
      b=$((b >> 1))
    done
  done
  echo $bits
}
PREFIX=$(prefix_len "$MASK")

ip link set "$IFACE" up
ip addr flush dev "$IFACE" 2>/dev/null || true
ip addr add "${ADDR}/${PREFIX}" dev "$IFACE"
# Add default route at metric 700 (wlan0 at 600 is preferred)
ip route add default via "$GW" dev "$IFACE" metric 700 2>/dev/null || true

# Register DNS with systemd-resolved. /etc/resolv.conf is a symlink to
# resolved's stub file on this system — appending to it directly is silently
# ignored/overwritten, so wwan0 never gets a resolver and DNS (and anything
# depending on it, e.g. NTP) breaks until this is set explicitly per-link.
if [ -n "$DNS" ] && command -v resolvectl >/dev/null 2>&1; then
  resolvectl dns "$IFACE" "$DNS"
  resolvectl domain "$IFACE" '~.'
fi

echo "wwan0 up: ${ADDR}/${PREFIX} gw=${GW} dns=${DNS}"

# Enable GNSS engine — output goes to /dev/ttyUSB_gnss (NMEA sentences)
# AT+CGPS=1,1 does not survive a power cycle, so we set it on every connect.
if [ -e "$AT_PORT" ]; then
  printf 'AT+CGPS=1,1\r\n' > "$AT_PORT"
  echo "gnss engine started on ${AT_PORT}"
fi
