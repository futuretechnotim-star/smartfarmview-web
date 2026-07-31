from field_node.connectivity_watchdog import should_reboot


def test_connected_never_reboots():
    assert should_reboot(connected=True, seconds_since_connected=10_000, timeout_s=900) is False


def test_disconnected_under_timeout_does_not_reboot():
    assert should_reboot(connected=False, seconds_since_connected=899, timeout_s=900) is False


def test_disconnected_past_timeout_reboots():
    assert should_reboot(connected=False, seconds_since_connected=901, timeout_s=900) is True


def test_disconnected_exactly_at_timeout_reboots():
    assert should_reboot(connected=False, seconds_since_connected=900, timeout_s=900) is True
