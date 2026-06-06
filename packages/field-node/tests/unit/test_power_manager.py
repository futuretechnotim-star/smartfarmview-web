from unittest.mock import patch

import pytest

from field_node.power.manager import PowerManager, PowerMode


@pytest.fixture
def pm():
    """PowerManager with system calls and solar daytime check patched out.

    Forcing nighttime isolates SoC-based behaviour from solar projection logic,
    which is time-of-day dependent and tested separately in TestSolarMode.
    """
    with (
        patch("field_node.power.manager._apply_mode"),
        patch.object(PowerManager, "_is_daytime", return_value=False),
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


class TestProperties:
    def test_camera_enabled_in_normal(self, pm: PowerManager) -> None:
        assert pm.camera_enabled is True

    def test_camera_enabled_in_eco(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(69)
        assert pm.camera_enabled is True

    def test_camera_enabled_in_low(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(49)
        assert pm.camera_enabled is True

    def test_camera_disabled_in_critical(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(24)
        assert pm.camera_enabled is False

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
