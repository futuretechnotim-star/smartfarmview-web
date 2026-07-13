#!/bin/bash
# Re-establish the LTE uplink if wwan0 has lost its default route.
#
# wwan0.service is a oneshot that only runs wwan-up.sh once at boot. If the
# modem drops and re-enumerates mid-session (new USB device, new cdc-wdm0 —
# seen after a marginal power event), nothing re-triggers it and wwan0 sits
# with no IP/route until the next reboot. This timer-driven check closes
# that gap by restarting wwan0.service whenever the route is missing.
set -e

IFACE=wwan0

if ip route show dev "$IFACE" | grep -q '^default'; then
    exit 0
fi

echo "wwan-watchdog: ${IFACE} has no default route, restarting wwan0.service"
systemctl restart wwan0.service
