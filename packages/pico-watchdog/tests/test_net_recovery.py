"""Tests for the connectivity self-heal timeout decision (net_recovery.py)."""

import net_recovery as nr

TIMEOUTS = {"timeout_wifi_down_s": 900.0, "timeout_wifi_up_s": 1800.0}


def should_reboot(offline_s, wifi_connected):
    return nr.should_reboot(offline_s, wifi_connected, **TIMEOUTS)


class TestWifiDown:
    def test_stays_false_below_threshold(self):
        assert should_reboot(899.9, wifi_connected=False) is False

    def test_true_at_threshold(self):
        assert should_reboot(900.0, wifi_connected=False) is True

    def test_true_above_threshold(self):
        assert should_reboot(1000.0, wifi_connected=False) is True


class TestWifiUp:
    # wifi up but MQTT down suggests a broker/HA-side hiccup (e.g. an update
    # in progress) rather than a wedged radio or hung Pi, so it gets a much
    # longer leash before the disruptive power-cycle.
    def test_stays_false_at_the_wifi_down_threshold(self):
        assert should_reboot(900.0, wifi_connected=True) is False

    def test_stays_false_below_threshold(self):
        assert should_reboot(1799.9, wifi_connected=True) is False

    def test_true_at_threshold(self):
        assert should_reboot(1800.0, wifi_connected=True) is True

    def test_true_above_threshold(self):
        assert should_reboot(2000.0, wifi_connected=True) is True
