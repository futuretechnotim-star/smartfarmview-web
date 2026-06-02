"""Tests for the LiFePO4 voltage → SoC estimate."""

from soc import lifepo4_soc


def test_full_above_curve():
    assert lifepo4_soc(13.8) == 100


def test_empty_below_curve():
    assert lifepo4_soc(11.5) == 0


def test_known_points():
    assert lifepo4_soc(13.30) == 90
    assert lifepo4_soc(13.00) == 40
    assert lifepo4_soc(12.80) == 20


def test_monotonic_non_decreasing_with_voltage():
    last = -1
    for mv in range(1150, 1380, 5):
        soc = lifepo4_soc(mv / 100.0)
        assert soc >= last
        last = soc


def test_interpolates_between_points():
    # Midpoint of 13.20 (80) and 13.10 (60) → ~70.
    soc = lifepo4_soc(13.15)
    assert 68 <= soc <= 72
