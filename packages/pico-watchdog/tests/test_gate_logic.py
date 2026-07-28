"""Tests for the autonomous power-gate state machine.

This is safety-critical logic — it decides when to cut and restore the gateway's
power — so it is covered directly even though the surrounding firmware is
MicroPython. ``gate_logic`` is pure (no imports), importable under CPython via
the ``firmware`` pythonpath entry.
"""

import gate_logic as gl

THRESHOLDS = {
    "shutdown_voltage": 12.5,
    "hard_cutoff_voltage": 12.0,
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
        # <= shutdown_voltage (12.5) triggers the graceful request (boundary inclusive).
        assert decide(gl.PI_ON, 12.5) == (gl.SHUTTING_DOWN, gl.ACTION_REQUEST_SHUTDOWN)

    def test_just_above_shutdown_threshold_stays_on(self):
        assert decide(gl.PI_ON, 12.6) == (gl.PI_ON, gl.ACTION_NONE)

    def test_stale_heartbeat_reboots(self):
        assert decide(gl.PI_ON, 13.4, hb_age=301) == (gl.PI_ON, gl.ACTION_REBOOT)

    def test_stale_heartbeat_no_reboot_when_mqtt_disconnected(self):
        # Regression (caught live at 13.5 V): the heartbeat comes over MQTT, so a
        # stale reading while the Pico's own broker link is down means the Pico
        # is blind, not that the Pi is hung. Rebooting then cycles the broker and
        # blocks reconnect — a self-sustaining ~5-min reboot loop. Suppress it.
        assert gl.decide(gl.PI_ON, 13.4, 0.0, 301.0, mqtt_connected=False, **THRESHOLDS) == (
            gl.PI_ON,
            gl.ACTION_NONE,
        )

    def test_stale_heartbeat_reboots_when_mqtt_connected(self):
        assert gl.decide(gl.PI_ON, 13.4, 0.0, 301.0, mqtt_connected=True, **THRESHOLDS) == (
            gl.PI_ON,
            gl.ACTION_REBOOT,
        )

    def test_low_voltage_beats_stale_heartbeat(self):
        # Graceful shutdown is preferred over a watchdog reboot.
        assert decide(gl.PI_ON, 11.5, hb_age=400) == (
            gl.SHUTTING_DOWN,
            gl.ACTION_REQUEST_SHUTDOWN,
        )


class TestShuttingDown:
    def test_waits_during_grace(self):
        # Above the hard-cutoff floor, still within grace → keep waiting to halt.
        assert decide(gl.SHUTTING_DOWN, 12.3, secs_in_state=10) == (
            gl.SHUTTING_DOWN,
            gl.ACTION_NONE,
        )

    def test_cuts_power_after_grace(self):
        # Grace elapsed with the Pi never confirming halt → cut (voltage above
        # the hard floor, so this isolates the grace-timeout path).
        assert decide(gl.SHUTTING_DOWN, 12.3, secs_in_state=90) == (gl.PI_OFF, gl.ACTION_CUT_POWER)

    def test_hard_cutoff_cuts_before_grace(self):
        # Battery hit the floor mid-shutdown → cut NOW, don't wait out grace.
        assert decide(gl.SHUTTING_DOWN, 11.9, secs_in_state=5) == (gl.PI_OFF, gl.ACTION_CUT_POWER)

    def test_at_hard_cutoff_boundary_cuts(self):
        # <= hard_cutoff_voltage (12.0) is inclusive.
        assert decide(gl.SHUTTING_DOWN, 12.0, secs_in_state=5) == (gl.PI_OFF, gl.ACTION_CUT_POWER)

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


class TestHaltConfirmed:
    """gpio-poweroff signal: the Pi's OS halt has actually completed."""

    def test_does_not_cut_running_pi_on_halt_confirmed(self):
        # Regression (caught live): a high halt_confirmed while PI_ON must NOT
        # cut a healthy, running Pi. On the field hardware GP18 latched high
        # after a shutdown because a back-power path kept the halted Pi alive;
        # honoring it in PI_ON would kill a Pi that's actually up. A genuine
        # unrequested halt is still caught by the stale-heartbeat reboot.
        assert gl.decide(gl.PI_ON, 13.4, 0.0, 0.0, halt_confirmed=True, **THRESHOLDS) == (
            gl.PI_ON,
            gl.ACTION_NONE,
        )

    def test_cuts_power_immediately_from_shutting_down_without_waiting_grace(self):
        # We DID request the shutdown, so trust the completion signal and cut
        # early instead of waiting out the full grace period.
        assert gl.decide(gl.SHUTTING_DOWN, 11.5, 1.0, 0.0, halt_confirmed=True, **THRESHOLDS) == (
            gl.PI_OFF,
            gl.ACTION_CUT_POWER,
        )

    def test_false_is_a_no_op_default(self):
        # Disconnected/absent wire must default to False and change nothing.
        assert decide(gl.PI_ON, 13.4) == (gl.PI_ON, gl.ACTION_NONE)

    def test_no_effect_once_already_off(self):
        assert gl.decide(gl.PI_OFF, 11.5, 0.0, 0.0, halt_confirmed=True, **THRESHOLDS) == (
            gl.PI_OFF,
            gl.ACTION_NONE,
        )

    def test_holds_off_during_settle_window_while_halt_confirmed(self):
        # Within the settle window, an asserted halt line still blocks restore
        # even at healthy voltage — don't re-power before the rail has dropped.
        assert gl.decide(
            gl.PI_OFF, 13.4, 5.0, 0.0, halt_confirmed=True, halt_settle_seconds=30.0, **THRESHOLDS
        ) == (gl.PI_OFF, gl.ACTION_NONE)

    def test_restores_after_settle_window_if_halt_confirmed_stuck(self):
        # The deadlock fix: past the settle window a still-asserted halt line is
        # treated as stuck (Pi kept alive by a back-power path) and must not
        # wedge the gateway off forever — voltage governs, so restore.
        assert gl.decide(
            gl.PI_OFF, 13.4, 40.0, 0.0, halt_confirmed=True, halt_settle_seconds=30.0, **THRESHOLDS
        ) == (gl.PI_ON, gl.ACTION_RESTORE_POWER)

    def test_stays_off_after_settle_if_voltage_below_recovery(self):
        # Even past the settle window, a genuinely low battery keeps it off —
        # recovery_voltage is the real guard.
        assert gl.decide(
            gl.PI_OFF, 13.0, 40.0, 0.0, halt_confirmed=True, halt_settle_seconds=30.0, **THRESHOLDS
        ) == (gl.PI_OFF, gl.ACTION_NONE)

    def test_restores_once_halt_confirmed_clears(self):
        assert gl.decide(gl.PI_OFF, 13.4, 0.0, 0.0, halt_confirmed=False, **THRESHOLDS) == (
            gl.PI_ON,
            gl.ACTION_RESTORE_POWER,
        )
