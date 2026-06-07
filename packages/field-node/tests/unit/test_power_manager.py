from unittest.mock import patch

import pytest

from field_node.power.manager import PowerManager, PowerMode


@pytest.fixture
def pm():
    """PowerManager with system calls, daytime check, and night-mode floor patched out.

    Forcing nighttime and suppressing the night floor isolates SoC-based behaviour
    from time-of-day concerns, which are tested separately in TestNightMode.
    """
    with (
        patch("field_node.power.manager._apply_mode"),
        patch.object(PowerManager, "_is_daytime", return_value=False),
        patch("field_node.power.manager.compute_night_mode", return_value=None),
    ):
        yield PowerManager()


def test_starts_in_normal_mode(pm: PowerManager) -> None:
    assert pm.mode == PowerMode.NORMAL


def test_no_system_call_on_init() -> None:
    with patch("field_node.power.manager._apply_mode") as mock_apply:
        PowerManager()
        mock_apply.assert_not_called()


class TestModeTransitions:
    def test_enters_eco_below_70(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode") as mock_apply:
            pm.update(69)
        assert pm.mode == PowerMode.ECO
        mock_apply.assert_called_once_with(PowerMode.ECO)

    def test_enters_low_below_50(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(49)
        assert pm.mode == PowerMode.LOW

    def test_enters_critical_below_25(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(24)
        assert pm.mode == PowerMode.CRITICAL

    def test_jumps_directly_to_critical_from_normal(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(10)
        assert pm.mode == PowerMode.CRITICAL

    def test_no_transition_at_boundary(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode") as mock_apply:
            pm.update(70)
        assert pm.mode == PowerMode.NORMAL
        mock_apply.assert_not_called()


class TestHysteresis:
    def test_stays_in_eco_below_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(69)  # enter ECO
            pm.update(72)  # above enter (70) but below exit (75) — stays ECO
        assert pm.mode == PowerMode.ECO

    def test_leaves_eco_at_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(69)  # enter ECO
            pm.update(75)  # at exit threshold — returns to NORMAL
        assert pm.mode == PowerMode.NORMAL

    def test_stays_in_low_below_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(49)  # enter LOW
            pm.update(52)  # above enter (50) but below exit (55) — stays LOW
        assert pm.mode == PowerMode.LOW

    def test_stays_in_critical_below_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(24)  # enter CRITICAL
            pm.update(27)  # above enter (25) but below exit (30) — stays CRITICAL
        assert pm.mode == PowerMode.CRITICAL


class TestDawnRecovery:
    def test_dawn_recovery_holds_low_after_depleted_night(self) -> None:
        """Daytime update with a low dawn SoC should be held at LOW even if SoC
        alone would allow ECO, because the battery hasn't recovered yet.
        """
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
        ):
            pm = PowerManager()
            # First call is the dawn transition — records dawn_soc_pct=40 (depleted)
            pm.update(40)
            # SoC rises to 60 — SoC-alone would be ECO, but dawn recovery holds LOW
            pm.update(60)
        assert pm.mode == PowerMode.LOW

    def test_dawn_recovery_lifts_once_recovered(self) -> None:
        """Once current SoC clears the recovery threshold, LOW constraint is lifted."""
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
        ):
            pm = PowerManager()
            pm.update(40)  # dawn — depleted
            pm.update(65)  # at recovery threshold → no dawn constraint
        # 65 is below ECO enter (70), so SoC mode drives ECO
        assert pm.mode == PowerMode.ECO


class TestNightMode:
    def test_night_floor_is_low_when_soc_is_high(self) -> None:
        """At full battery during nighttime, mode should still be at least LOW."""
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=False),
        ):
            pm = PowerManager()
            pm.update(100)
        assert pm.mode == PowerMode.LOW

    def test_critical_battery_at_night_stays_critical(self) -> None:
        """Night floor does not override a battery-driven CRITICAL escalation."""
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=False),
        ):
            pm = PowerManager()
            pm.update(10)
        assert pm.mode == PowerMode.CRITICAL

    def test_camera_disabled_at_night_regardless_of_mode(self) -> None:
        """camera_enabled is False at night even when mode is NORMAL/ECO/LOW."""
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=False),
        ):
            pm = PowerManager()
        assert pm.camera_enabled is False

    def test_camera_enabled_during_day(self) -> None:
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
        ):
            pm = PowerManager()
            pm.update(100)
            assert pm.camera_enabled is True


class TestProperties:
    def test_camera_enabled_in_normal_daytime(self) -> None:
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
        ):
            pm = PowerManager()
            pm.update(100)
            assert pm.camera_enabled is True

    def test_camera_enabled_in_eco_daytime(self) -> None:
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
            patch("field_node.power.manager.compute_dawn_recovery_mode", return_value=None),
        ):
            pm = PowerManager()
            # Force SoC into ECO range — solar_no_data would escalate to ECO anyway,
            # but set a low SoC explicitly to test the SoC path
            pm._soc_mode = PowerMode.ECO  # type: ignore[attr-defined]
            pm._mode = PowerMode.ECO  # type: ignore[attr-defined]
            assert pm.camera_enabled is True

    def test_camera_enabled_in_low_daytime(self) -> None:
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
        ):
            pm = PowerManager()
            pm._mode = PowerMode.LOW  # type: ignore[attr-defined]
            assert pm.camera_enabled is True

    def test_camera_disabled_in_critical(self, pm: PowerManager) -> None:
        with (
            patch("field_node.power.manager._apply_mode"),
            patch.object(PowerManager, "_is_daytime", return_value=True),
        ):
            pm2 = PowerManager()
            pm2._mode = PowerMode.CRITICAL  # type: ignore[attr-defined]
            assert pm2.camera_enabled is False

    def test_camera_standby_only_in_low(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(49)
        assert pm.camera_standby_between_captures is True

    def test_camera_standby_in_eco(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(69)
        assert pm.camera_standby_between_captures is True

    def test_telemetry_interval_normal(self, pm: PowerManager) -> None:
        assert pm.telemetry_interval_seconds == 60  # settings default

    def test_telemetry_interval_floors_in_low(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(49)
        assert pm.telemetry_interval_seconds == 300

    def test_telemetry_interval_floors_in_critical(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(24)
        assert pm.telemetry_interval_seconds == 600
