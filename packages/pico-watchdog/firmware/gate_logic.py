"""Pure power-gate state machine for the Pico 2 W watchdog.

This is the **autonomous safety core**: given the battery voltage, how long the
gate has been in its current state, and how stale the Pi's heartbeat is, it
decides the next state and the single hardware action to take. It has **no I/O
and no imports** so it runs identically on MicroPython (on the Pico) and on
CPython (under pytest) — the hardware glue in ``main.py`` reads sensors, calls
:func:`decide`, and executes the returned action.

Design rules baked in here:

* The gate's hard-cutoff voltage MUST sit *below* the gateway software's
  CRITICAL threshold, so Home Assistant always gets first chance to shed load
  and halt gracefully before the gate cuts power.
* Recovery uses a higher voltage than shutdown (hysteresis) so the Pi doesn't
  boot-loop on a sagging battery at dawn.
* A hung Pi (stale heartbeat) is power-cycled, but only while it is supposed to
  be running (state ``PI_ON``).
"""

# --- States -----------------------------------------------------------------
PI_ON = "pi_on"  # Pi powered and expected to be running
SHUTTING_DOWN = "shutting_down"  # shutdown requested; waiting out the grace period
PI_OFF = "pi_off"  # power cut; waiting for the battery to recover

# --- Actions (executed by the hardware layer) -------------------------------
ACTION_NONE = "none"
ACTION_REQUEST_SHUTDOWN = "request_shutdown"  # assert SHUTDOWN_REQ line to the Pi
ACTION_CUT_POWER = "cut_power"  # disable the 5V buck enable
ACTION_RESTORE_POWER = "restore_power"  # re-enable the 5V buck (wake-on-recharge)
ACTION_REBOOT = "reboot"  # power-cycle a hung Pi (watchdog)


def decide(
    state: str,
    voltage_v: float,
    seconds_in_state: float,
    heartbeat_age_s: float,
    *,
    shutdown_voltage: float,
    recovery_voltage: float,
    grace_seconds: float,
    heartbeat_timeout_s: float,
) -> "tuple[str, str]":
    """Return ``(new_state, action)`` for the current inputs.

    ``heartbeat_age_s`` is seconds since the last Pi heartbeat; pass a small
    value (e.g. just after a reboot) to indicate a healthy, recently-heard Pi.
    """
    if state == PI_ON:
        # Low battery wins over the watchdog — always prefer a graceful halt.
        if voltage_v <= shutdown_voltage:
            return SHUTTING_DOWN, ACTION_REQUEST_SHUTDOWN
        if heartbeat_age_s > heartbeat_timeout_s:
            # Hung Pi: power-cycle it. Stay in PI_ON; the hardware layer resets
            # the heartbeat clock after the pulse so we don't re-trigger.
            return PI_ON, ACTION_REBOOT
        return PI_ON, ACTION_NONE

    if state == SHUTTING_DOWN:
        if seconds_in_state >= grace_seconds:
            return PI_OFF, ACTION_CUT_POWER
        return SHUTTING_DOWN, ACTION_NONE

    if state == PI_OFF:
        if voltage_v >= recovery_voltage:
            return PI_ON, ACTION_RESTORE_POWER
        return PI_OFF, ACTION_NONE

    # Unknown state — fail safe to the powered-on state without acting.
    return PI_ON, ACTION_NONE
