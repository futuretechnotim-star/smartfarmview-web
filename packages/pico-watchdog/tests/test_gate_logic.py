"""Tests for the autonomous power-gate state machine.

This is safety-critical logic — it decides when to cut and restore the gateway's
power — so it is covered directly even though the surrounding firmware is
MicroPython. ``gate_logic`` is pure (no imports), importable under CPython via
the ``firmware`` pythonpath entry.
"""

import gate_logic as gl

THRESHOLDS = {
    "shutdown_voltage": 12.0,
    "recovery_voltage": 13.2,
    "grace_seconds": 90.0,
    "heartbeat_timeout_s": 300.0,
}


def decide(state, voltage, secs_in_state=0.0, hb_age=0.0):
    return gl.decide(state, voltage, secs_in_state, hb_age, **THRESHOLDS)


class TestPiOn:
    def test_healthy_no_action(self):
        assert decide(gl.PI_ON, 13.4) == (gl.PI_ON, gl.ACTION_NONE)

    def test_low_voltage_requests_shutdown(self):
        assert decide(gl.PI_ON, 11.9) == (gl.SHUTTING_DOWN, gl.ACTION_REQUEST_SHUTDOWN)

    def test_at_shutdown_threshold_requests_shutdown(self):
        # <= shutdown_voltage triggers (boundary inclusive).
        assert decide(gl.PI_ON, 12.0) == (gl.SHUTTING_DOWN, gl.ACTION_REQUEST_SHUTDOWN)

    def test_stale_heartbeat_reboots(self):
        assert decide(gl.PI_ON, 13.4, hb_age=301) == (gl.PI_ON, gl.ACTION_REBOOT)

    def test_low_voltage_beats_stale_heartbeat(self):
        # Graceful shutdown is preferred over a watchdog reboot.
        assert decide(gl.PI_ON, 11.5, hb_age=400) == (
            gl.SHUTTING_DOWN,
            gl.ACTION_REQUEST_SHUTDOWN,
        )


class TestShuttingDown:
    def test_waits_during_grace(self):
        assert decide(gl.SHUTTING_DOWN, 11.5, secs_in_state=10) == (
            gl.SHUTTING_DOWN,
            gl.ACTION_NONE,
        )

    def test_cuts_power_after_grace(self):
        assert decide(gl.SHUTTING_DOWN, 11.5, secs_in_state=90) == (gl.PI_OFF, gl.ACTION_CUT_POWER)

    def test_completes_shutdown_even_if_voltage_recovers(self):
        # Once committed, don't abort mid-shutdown — the Pi already got the signal.
        assert decide(gl.SHUTTING_DOWN, 13.5, secs_in_state=30) == (
            gl.SHUTTING_DOWN,
            gl.ACTION_NONE,
        )


class TestPiOff:
    def test_stays_off_below_recovery(self):
        assert decide(gl.PI_OFF, 13.0) == (gl.PI_OFF, gl.ACTION_NONE)

    def test_restores_at_recovery(self):
        assert decide(gl.PI_OFF, 13.2) == (gl.PI_ON, gl.ACTION_RESTORE_POWER)

    def test_hysteresis_no_bootloop(self):
        # Just above shutdown but below recovery → stay off (prevents boot-loop).
        assert decide(gl.PI_OFF, 12.5) == (gl.PI_OFF, gl.ACTION_NONE)


def test_unknown_state_fails_safe_on():
    assert decide("bogus", 13.0) == (gl.PI_ON, gl.ACTION_NONE)
