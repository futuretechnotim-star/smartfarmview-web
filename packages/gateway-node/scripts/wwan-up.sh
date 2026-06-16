#!/bin/bash
# Bring up LTE data connection on wwan0 via QMI.
# Called by wwan0.service on boot.
set -e

DEV=/dev/cdc-wdm0
IFACE=wwan0
AT_PORT=/dev/ttyUSB_at

# Ensure ModemManager has detected the modem (may need a udev nudge at boot)
udevadm trigger --action=add --sysname-match='ttyUSB*'
sleep 5

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

# Push DNS to resolv.conf if not already present
if [ -n "$DNS" ] && ! grep -q "$DNS" /etc/resolv.conf 2>/dev/null; then
  echo "nameserver $DNS" | tee -a /etc/resolv.conf > /dev/null
fi

echo "wwan0 up: ${ADDR}/${PREFIX} gw=${GW} dns=${DNS}"

# Enable GNSS engine — output goes to /dev/ttyUSB_gnss (NMEA sentences)
# AT+CGPS=1,1 does not survive a power cycle, so we set it on every connect.
if [ -e "$AT_PORT" ]; then
  printf 'AT+CGPS=1,1\r\n' > "$AT_PORT"
  echo "gnss engine started on ${AT_PORT}"
fi
