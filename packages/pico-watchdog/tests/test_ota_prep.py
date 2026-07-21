"""Tests for the graceful-shutdown-before-OTA wait/proceed/abort decision."""

import ota_prep as op


def test_proceeds_once_halt_confirmed():
    assert op.decide_ota_wait(True, 5.0, 120.0) == op.PROCEED


def test_proceeds_immediately_even_at_zero_elapsed():
    assert op.decide_ota_wait(True, 0.0, 120.0) == op.PROCEED


def test_waits_before_timeout_without_confirmation():
    assert op.decide_ota_wait(False, 10.0, 120.0) == op.WAIT


def test_aborts_at_timeout_without_confirmation():
    assert op.decide_ota_wait(False, 120.0, 120.0) == op.ABORT


def test_aborts_past_timeout_without_confirmation():
    assert op.decide_ota_wait(False, 121.0, 120.0) == op.ABORT


def test_halt_confirmed_wins_even_at_exact_timeout():
    # Confirmation arriving in the same tick as the timeout should still count.
    assert op.decide_ota_wait(True, 120.0, 120.0) == op.PROCEED
