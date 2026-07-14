#!/bin/bash
# Quality-based backhaul selection between wlan0 (WiFi: WebsterFiber /
# WebSTARLINK) and wwan0 (LTE).
#
# Both interfaces normally carry their own default route simultaneously
# (wlan0 at metric 600, wwan0 at metric 700, set by NetworkManager/DHCP and
# wwan-up.sh respectively) — that static ordering is what shipped originally,
# and it fails exactly the way the WebsterFiber outage did: a route can stay
# "up" while being completely dead, and nothing ever re-checks it.
#
# This script never creates or deletes a default route — it only re-orders
# the metrics of routes that already exist, so a bug here can't leave the
# box with zero path out. wwan-watchdog.timer is what's responsible for
# wwan0 actually having a route in the first place; NetworkManager handles
# the same for wlan0.
set -u

CANDIDATES="wlan0 wwan0"
PING_TARGETS="1.1.1.1 8.8.8.8"
PING_COUNT=3
PING_DEADLINE=5
DEAD_LOSS_PCT=100
SWITCH_STREAK_REQUIRED=3
STATE_FILE=/run/backhaul-select.state
BASE_METRIC=600
METRIC_STEP=100

# WiFi (WebsterFiber/Starlink) is unmetered, LTE typically isn't — so a
# marginal latency win for wwan0 shouldn't bounce primary away from an
# otherwise-healthy wlan0. While wlan0's own loss stays at/under
# PREFERRED_LOSS_OK_PCT, it gets a latency discount for ranking purposes
# only; a genuinely degraded wlan0 (high loss, or dead — see DEAD_LOSS_PCT
# above) still loses normally.
PREFERRED_IFACE="wlan0"
PREFERRED_LOSS_OK_PCT=20
PREFERRED_LATENCY_BIAS_MS=150

log() { echo "backhaul-select: $*"; }

# health_check <iface> -> prints "<loss_pct> <avg_rtt_ms>" (rtt=9999 if unknown)
health_check() {
    local iface="$1" best_loss=100 best_rtt=9999
    for target in $PING_TARGETS; do
        local out loss rtt
        out=$(ping -I "$iface" -c "$PING_COUNT" -W 2 -w "$PING_DEADLINE" -q "$target" 2>/dev/null)
        loss=$(echo "$out" | awk -F',' '/packet loss/ {gsub(/[^0-9]/,"",$3); print $3}')
        rtt=$(echo "$out" | awk -F'/' '/rtt|round-trip/ {print $5}')
        [ -z "$loss" ] && loss=100
        [ -z "$rtt" ] && rtt=9999
        if [ "$loss" -lt "$best_loss" ] || { [ "$loss" -eq "$best_loss" ] && awk "BEGIN{exit !($rtt<$best_rtt)}"; }; then
            best_loss=$loss
            best_rtt=$rtt
        fi
    done
    echo "$best_loss $best_rtt"
}

# current_gateway <iface> -> gateway IP of its existing default route, empty if none
current_gateway() {
    ip route show default dev "$1" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1)}' | head -1
}

STATE_ACTIVE=""
STATE_CANDIDATE=""
STATE_STREAK=0
if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    . "$STATE_FILE"
fi

usable_ifaces=""
declare -A LOSS RTT GW
for iface in $CANDIDATES; do
    gw=$(current_gateway "$iface")
    if [ -z "$gw" ]; then
        log "${iface}: no default route, skipping"
        continue
    fi
    read -r loss rtt <<< "$(health_check "$iface")"
    log "${iface}: loss=${loss}% rtt=${rtt}ms gw=${gw}"
    LOSS[$iface]=$loss
    RTT[$iface]=$rtt
    GW[$iface]=$gw
    usable_ifaces="$usable_ifaces $iface"
done

if [ -z "$usable_ifaces" ]; then
    log "no candidate interfaces have a default route at all, nothing to do"
    exit 0
fi

# Rank usable interfaces: lowest loss first, then lowest (bias-adjusted) rtt.
ranked=$(for iface in $usable_ifaces; do
    eff_rtt=${RTT[$iface]}
    if [ "$iface" = "$PREFERRED_IFACE" ] && [ "${LOSS[$iface]}" -le "$PREFERRED_LOSS_OK_PCT" ]; then
        eff_rtt=$(awk "BEGIN{print ${RTT[$iface]} - $PREFERRED_LATENCY_BIAS_MS}")
    fi
    echo "${LOSS[$iface]} ${eff_rtt} $iface"
done | sort -n -k1,1 -k2,2 | awk '{print $3}')

best=$(echo "$ranked" | head -1)

# Prefer our own last decision (STATE_ACTIVE) over re-deriving primary from
# the live route table: NetworkManager re-asserts its own DHCP-learned
# metric on wlan0 independently of this script, which can otherwise leave
# two default routes for the same interface at different metrics and make
# the live table an unreliable/ambiguous source of truth. Only fall back to
# a live-table read on the very first run (no state yet); if our last
# choice dropped out of the candidate set entirely, treat it as gone.
if [ -n "$STATE_ACTIVE" ] && [ -n "${GW[$STATE_ACTIVE]+x}" ]; then
    current_primary=$STATE_ACTIVE
elif [ -z "$STATE_ACTIVE" ]; then
    current_primary=$(ip route show default | awk '
        {
            dev=""; metric=999999
            for (i=1; i<=NF; i++) {
                if ($i == "dev") dev = $(i+1)
                if ($i == "metric") metric = $(i+1)
            }
            if (dev != "") print metric, dev
        }' | sort -n -k1,1 | head -1 | awk '{print $2}')
else
    current_primary=""
fi

if [ "$best" = "$current_primary" ] || [ -z "$current_primary" ]; then
    STATE_CANDIDATE=""
    STATE_STREAK=0
    STATE_ACTIVE=$best
else
    current_loss=${LOSS[$current_primary]:-100}
    if [ "$current_loss" -ge "$DEAD_LOSS_PCT" ]; then
        log "current primary ${current_primary} is dead (loss=${current_loss}%), switching immediately to ${best}"
        STATE_CANDIDATE=$best
        STATE_STREAK=$SWITCH_STREAK_REQUIRED
    elif [ "$best" = "$STATE_CANDIDATE" ]; then
        STATE_STREAK=$((STATE_STREAK + 1))
        log "${best} has been better than ${current_primary} for ${STATE_STREAK}/${SWITCH_STREAK_REQUIRED} checks"
    else
        STATE_CANDIDATE=$best
        STATE_STREAK=1
        log "${best} is now the top candidate over ${current_primary} (1/${SWITCH_STREAK_REQUIRED})"
    fi

    if [ "$STATE_STREAK" -ge "$SWITCH_STREAK_REQUIRED" ]; then
        log "switching primary from ${current_primary} to ${STATE_CANDIDATE}"
        STATE_ACTIVE=$STATE_CANDIDATE
        STATE_CANDIDATE=""
        STATE_STREAK=0
    else
        STATE_ACTIVE=$current_primary
    fi
fi

# Always re-apply the full metric ordering for STATE_ACTIVE, every cycle —
# self-heals if NetworkManager (or anything else) silently reset a route's
# metric between runs, which is exactly the kind of drift that caused the
# original WebsterFiber incident. Fully clear each interface's existing
# default route(s) first: `ip route replace` only replaces a route matching
# the same metric, so a stale entry at a different metric (e.g. a prior
# cycle's assignment, or NetworkManager's own) would otherwise linger
# alongside the new one instead of being removed.
metric=$BASE_METRIC
log "enforcing route ordering, primary=${STATE_ACTIVE}"
for iface in $STATE_ACTIVE $(echo "$ranked" | grep -v "^${STATE_ACTIVE}\$"); do
    while :; do
        old_gw=$(ip route show default dev "$iface" 2>/dev/null | head -1 | awk '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1)}')
        [ -z "$old_gw" ] && break
        ip route del default via "$old_gw" dev "$iface" 2>/dev/null || break
    done
    ip route add default via "${GW[$iface]}" dev "$iface" metric "$metric" 2>/dev/null \
        && log "  ${iface} -> metric ${metric}"
    metric=$((metric + METRIC_STEP))
done

cat > "$STATE_FILE" << EOF
STATE_ACTIVE=$STATE_ACTIVE
STATE_CANDIDATE=$STATE_CANDIDATE
STATE_STREAK=$STATE_STREAK
EOF
