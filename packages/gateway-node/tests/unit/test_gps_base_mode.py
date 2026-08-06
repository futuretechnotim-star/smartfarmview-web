"""Tests for the pure RTK base-station state machine (gps_base_mode.py)."""

from gateway_node.gps_base_mode import (
    ACTION_ENTER_BASE,
    ACTION_EXIT_BASE,
    ACTION_NONE,
    ACTION_START_SURVEY,
    BASE_ACTIVE,
    CMD_START_SURVEY,
    CMD_STOP_BASE,
    ROVER_NAV,
    SURVEYING,
    decide,
)
from gateway_node.gps_protocol import SvinStatus


def _svin(valid: bool, active: bool = True, dur_s: int = 60, mean_acc_m: float = 1.0):
    return SvinStatus(dur_s=dur_s, mean_acc_m=mean_acc_m, valid=valid, active=active)


class TestRoverNav:
    def test_stays_rover_nav_with_no_command(self):
        assert decide(ROVER_NAV, None, None) == (ROVER_NAV, ACTION_NONE)

    def test_start_survey_command_arms_survey_in(self):
        assert decide(ROVER_NAV, CMD_START_SURVEY, None) == (SURVEYING, ACTION_START_SURVEY)

    def test_stop_command_is_a_no_op_already_in_rover_nav(self):
        assert decide(ROVER_NAV, CMD_STOP_BASE, None) == (ROVER_NAV, ACTION_NONE)


class TestSurveying:
    def test_stays_surveying_with_no_svin_status_yet(self):
        assert decide(SURVEYING, None, None) == (SURVEYING, ACTION_NONE)

    def test_stays_surveying_while_svin_not_valid(self):
        assert decide(SURVEYING, None, _svin(valid=False)) == (SURVEYING, ACTION_NONE)

    def test_transitions_to_base_active_once_svin_valid(self):
        assert decide(SURVEYING, None, _svin(valid=True)) == (BASE_ACTIVE, ACTION_ENTER_BASE)

    def test_stop_command_aborts_survey_back_to_rover_nav(self):
        assert decide(SURVEYING, CMD_STOP_BASE, _svin(valid=False)) == (ROVER_NAV, ACTION_EXIT_BASE)

    def test_stop_command_wins_even_if_svin_just_went_valid(self):
        assert decide(SURVEYING, CMD_STOP_BASE, _svin(valid=True)) == (ROVER_NAV, ACTION_EXIT_BASE)


class TestBaseActive:
    def test_stays_base_active_with_no_command(self):
        assert decide(BASE_ACTIVE, None, None) == (BASE_ACTIVE, ACTION_NONE)

    def test_stop_command_exits_base_mode(self):
        assert decide(BASE_ACTIVE, CMD_STOP_BASE, None) == (ROVER_NAV, ACTION_EXIT_BASE)

    def test_start_survey_command_is_ignored_already_active(self):
        assert decide(BASE_ACTIVE, CMD_START_SURVEY, None) == (BASE_ACTIVE, ACTION_NONE)


class TestUnknownState:
    def test_fails_safe_to_rover_nav(self):
        assert decide("bogus_state", None, None) == (ROVER_NAV, ACTION_EXIT_BASE)
