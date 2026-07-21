"""Tests for the enclosure-fan thermostat state machine."""

import fan_logic as fl

THRESHOLDS = {"on_temp_c": 35.0, "off_temp_c": 30.0}


def decide(fan_on, temp_c):
    return fl.decide(fan_on, temp_c, **THRESHOLDS)


class TestFanOff:
    def test_stays_off_below_on_threshold(self):
        assert decide(fl.FAN_OFF, 34.9) == fl.FAN_OFF

    def test_turns_on_at_threshold(self):
        assert decide(fl.FAN_OFF, 35.0) == fl.FAN_ON

    def test_turns_on_above_threshold(self):
        assert decide(fl.FAN_OFF, 40.0) == fl.FAN_ON


class TestFanOn:
    def test_stays_on_above_off_threshold(self):
        assert decide(fl.FAN_ON, 32.0) == fl.FAN_ON

    def test_stays_on_at_off_threshold_boundary(self):
        # off_temp_c is inclusive-off, so exactly at it the fan should stop.
        assert decide(fl.FAN_ON, 30.0) == fl.FAN_OFF

    def test_turns_off_below_off_threshold(self):
        assert decide(fl.FAN_ON, 29.0) == fl.FAN_OFF

    def test_hysteresis_no_chatter_between_thresholds(self):
        # Between off and on thresholds, an already-running fan keeps running.
        assert decide(fl.FAN_ON, 32.5) == fl.FAN_ON
