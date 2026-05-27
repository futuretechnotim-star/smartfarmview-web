from unittest.mock import patch

import pytest

from field_node.power.manager import PowerManager, PowerMode


@pytest.fixture
def pm():
    """PowerManager with system calls patched out."""
    with patch("field_node.power.manager._apply_mode"):
        yield PowerManager()


def test_starts_in_normal_mode(pm: PowerManager) -> None:
    assert pm.mode == PowerMode.NORMAL


def test_no_system_call_on_init() -> None:
    with patch("field_node.power.manager._apply_mode") as mock_apply:
        PowerManager()
        mock_apply.assert_not_called()


class TestModeTransitions:
    def test_enters_eco_below_60(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode") as mock_apply:
            pm.update(59)
        assert pm.mode == PowerMode.ECO
        mock_apply.assert_called_once_with(PowerMode.ECO)

    def test_enters_low_below_40(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(39)
        assert pm.mode == PowerMode.LOW

    def test_enters_critical_below_20(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(19)
        assert pm.mode == PowerMode.CRITICAL

    def test_jumps_directly_to_critical_from_normal(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(10)
        assert pm.mode == PowerMode.CRITICAL

    def test_no_transition_at_boundary(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode") as mock_apply:
            pm.update(60)
        assert pm.mode == PowerMode.NORMAL
        mock_apply.assert_not_called()


class TestHysteresis:
    def test_stays_in_eco_below_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(59)  # enter ECO
            pm.update(63)  # above enter (60) but below exit (65) — stays ECO
        assert pm.mode == PowerMode.ECO

    def test_leaves_eco_at_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(59)  # enter ECO
            pm.update(65)  # at exit threshold — returns to NORMAL
        assert pm.mode == PowerMode.NORMAL

    def test_stays_in_low_below_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(39)  # enter LOW
            pm.update(43)  # above enter (40) but below exit (45) — stays LOW
        assert pm.mode == PowerMode.LOW

    def test_stays_in_critical_below_exit_threshold(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(19)  # enter CRITICAL
            pm.update(23)  # above enter (20) but below exit (25) — stays CRITICAL
        assert pm.mode == PowerMode.CRITICAL


class TestProperties:
    def test_camera_enabled_in_normal(self, pm: PowerManager) -> None:
        assert pm.camera_enabled is True

    def test_camera_enabled_in_eco(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(59)
        assert pm.camera_enabled is True

    def test_camera_enabled_in_low(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(39)
        assert pm.camera_enabled is True

    def test_camera_disabled_in_critical(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(19)
        assert pm.camera_enabled is False

    def test_camera_standby_only_in_low(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(39)
        assert pm.camera_standby_between_captures is True

    def test_camera_standby_not_in_eco(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(59)
        assert pm.camera_standby_between_captures is False

    def test_telemetry_interval_normal(self, pm: PowerManager) -> None:
        assert pm.telemetry_interval_seconds == 60  # settings default

    def test_telemetry_interval_floors_in_low(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(39)
        assert pm.telemetry_interval_seconds == 300

    def test_telemetry_interval_floors_in_critical(self, pm: PowerManager) -> None:
        with patch("field_node.power.manager._apply_mode"):
            pm.update(19)
        assert pm.telemetry_interval_seconds == 600
