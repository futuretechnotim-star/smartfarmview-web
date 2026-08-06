#!/bin/bash
# Re-establish the LTE uplink if wwan0 has lost its default route, OR if the
# route is present but the link is actually dead.
#
# wwan0.service is a oneshot that only runs wwan-up.sh once at boot. If the
# modem drops and re-enumerates mid-session (new USB device, new cdc-wdm0 —
# seen after a marginal power event), nothing re-triggers it and wwan0 sits
# with no IP/route until the next reboot. This timer-driven check closes
# that gap by restarting wwan0.service whenever the route is missing.
#
# Route existence alone isn't enough, though: on 2026-08-06 the SIM7600G-H's
# QMI data session silently died (100% packet loss) while the interface kept
# its route and ModemManager/kernel logged nothing — a "zombie" bearer. This
# route-only check reported healthy every 2 minutes for ~14 hours straight
# while backhaul-select.sh's independent ping-based health check (run every
# ~30s for route-selection purposes) saw dead the whole time. wwan0 was the
# only backhaul candidate configured at the time, so there was nothing to
# fail over to and nothing ever restarted the modem. See docs/gateway-node.md
# and the 2026-08-06 incident notes.
#
# Fix: actually ping through the interface, not just check the route exists.
# Require CONSECUTIVE_FAILURES_REQUIRED bad checks in a row (state persisted
# across runs) before restarting, so one transient dropped ping — plausible
# on LTE — doesn't trigger a needless modem reset; genuine death (like the
# 14h zombie session) still gets caught within a few minutes instead of
# running until the next manual/pico power-cycle.
set -u

IFACE=wwan0
PING_TARGETS="1.1.1.1 8.8.8.8"
PING_COUNT=3
PING_DEADLINE=5
DEAD_LOSS_PCT=100
CONSECUTIVE_FAILURES_REQUIRED=2
STATE_FILE=/run/wwan-watchdog.state

log() { echo "wwan-watchdog: $*"; }

restart_wwan0() {
    log "restarting wwan0.service (${1})"
    systemctl restart wwan0.service
    rm -f "$STATE_FILE"
}

if ! ip route show dev "$IFACE" | grep -q '^default'; then
    restart_wwan0 "no default route"
    exit 0
fi

# health_check mirrors backhaul-select.sh's function — same targets/timeouts,
# kept as a separate copy since the two scripts run independently on
# different timers and neither should depend on the other's presence.
best_loss=100
for target in $PING_TARGETS; do
    out=$(ping -I "$IFACE" -c "$PING_COUNT" -W 2 -w "$PING_DEADLINE" -q "$target" 2>/dev/null)
    loss=$(echo "$out" | awk -F',' '/packet loss/ {gsub(/[^0-9]/,"",$3); print $3}')
    [ -z "$loss" ] && loss=100
    [ "$loss" -lt "$best_loss" ] && best_loss=$loss
done

if [ "$best_loss" -lt "$DEAD_LOSS_PCT" ]; then
    log "${IFACE} reachable (loss=${best_loss}%)"
    rm -f "$STATE_FILE"
    exit 0
fi

streak=0
[ -f "$STATE_FILE" ] && streak=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
streak=$((streak + 1))

if [ "$streak" -ge "$CONSECUTIVE_FAILURES_REQUIRED" ]; then
    restart_wwan0 "${IFACE} unreachable (loss=100%) for ${streak} consecutive checks"
else
    log "${IFACE} unreachable (loss=100%), ${streak}/${CONSECUTIVE_FAILURES_REQUIRED} consecutive checks — not restarting yet"
    echo "$streak" > "$STATE_FILE"
fi
