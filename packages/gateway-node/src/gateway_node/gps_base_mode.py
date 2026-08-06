"""Pure RTK base-station mode state machine for the gateway's ZED-F9P.

Same shape as pico-watchdog's gate_logic.py / fan_logic.py: given the current
state and the latest survey-in status, decide the next state and the single
hardware action to take. No I/O, no imports — the hardware glue (I2C
CFG-VALSET writes, NAV-SVIN polling) lives in gps_driver.py.

    ROVER_NAV ──(start_survey cmd)──► SURVEYING ──(svin valid)──► BASE_ACTIVE
       ▲                                                                │
       └────────────────────(stop cmd)─────────────────────────────────┘

NAV-PVT and RTCM3 output on I2C are never both enabled — that's the exact
DDC-buffer-overrun failure mode documented in docs/gpsrtk.md. ROVER_NAV
streams NAV-PVT only; SURVEYING streams NAV-SVIN only (NAV-PVT off); once the
module reports svinValid, BASE_ACTIVE turns NAV-SVIN off and RTCM3 on. The
module's own survey-in result becomes its implicit fixed reference — no
separate explicit-coordinates FIXED-mode write is needed.
"""

from __future__ import annotations

from gateway_node.gps_protocol import SvinStatus

ROVER_NAV = "rover_nav"
SURVEYING = "surveying"
BASE_ACTIVE = "base_active"

ACTION_NONE = "none"
ACTION_START_SURVEY = "start_survey"  # arm TMODE3 survey-in; NAV-PVT off, NAV-SVIN on
ACTION_ENTER_BASE = "enter_base"  # NAV-SVIN off, RTCM3 on
ACTION_EXIT_BASE = "exit_base"  # TMODE3 disabled; RTCM3 off, NAV-PVT back on

CMD_START_SURVEY = "gps_start_survey"
CMD_STOP_BASE = "gps_stop_base"


def decide(
    state: str,
    cmd: str | None,
    svin: SvinStatus | None,
) -> tuple[str, str]:
    """Return ``(new_state, action)`` for the current state and inputs.

    ``cmd`` is the MQTT command received this tick (CMD_START_SURVEY /
    CMD_STOP_BASE), or None if none arrived. ``svin`` is the most recently
    polled NAV-SVIN status while SURVEYING, or None if not yet available.

    The stop command works from any non-ROVER_NAV state — it's the operator's
    manual escape hatch out of a stalled survey or an active base, matching
    prepare_shutdown/capture's existing fire-and-forget MQTT command pattern.
    """
    if cmd == CMD_STOP_BASE and state != ROVER_NAV:
        return ROVER_NAV, ACTION_EXIT_BASE

    if state == ROVER_NAV:
        if cmd == CMD_START_SURVEY:
            return SURVEYING, ACTION_START_SURVEY
        return ROVER_NAV, ACTION_NONE

    if state == SURVEYING:
        if svin is not None and svin.valid:
            return BASE_ACTIVE, ACTION_ENTER_BASE
        return SURVEYING, ACTION_NONE

    if state == BASE_ACTIVE:
        return BASE_ACTIVE, ACTION_NONE

    # Unknown state — fail safe to the everyday rover/NAV-PVT behavior.
    return ROVER_NAV, ACTION_EXIT_BASE
