"""Tests for the pure power-budget policy.

Mirrors the SoC-transition / hysteresis expectations that previously lived in
field-node's ``test_power_manager.py`` (now that the decision logic is shared),
plus direct coverage of the solar projection and combine helpers.
"""

import pytest

from power_policy import (
    PowerMode,
    combine_modes,
    compute_dawn_recovery_mode,
    compute_solar_mode,
    evaluate_soc_mode,
    severity_index,
    stricter,
)


class TestSeverity:
    def test_ordering(self) -> None:
        assert severity_index(PowerMode.NORMAL) < severity_index(PowerMode.ECO)
        assert severity_index(PowerMode.ECO) < severity_index(PowerMode.LOW)
        assert severity_index(PowerMode.LOW) < severity_index(PowerMode.CRITICAL)

    def test_stricter_picks_more_severe(self) -> None:
        assert stricter(PowerMode.ECO, PowerMode.CRITICAL) == PowerMode.CRITICAL
        assert stricter(PowerMode.LOW, PowerMode.ECO) == PowerMode.LOW

    def test_stricter_with_none(self) -> None:
        assert stricter(PowerMode.LOW, None) == PowerMode.LOW


class TestEvaluateSocMode:
    @pytest.mark.parametrize(
        ("soc", "expected"),
        [
            (100, PowerMode.NORMAL),
            (70, PowerMode.NORMAL),  # boundary: enter ECO is < 70
            (69, PowerMode.ECO),
            (50, PowerMode.ECO),  # boundary: enter LOW is < 50
            (49, PowerMode.LOW),
            (25, PowerMode.LOW),  # boundary: enter CRITICAL is < 25
            (24, PowerMode.CRITICAL),
            (10, PowerMode.CRITICAL),
        ],
    )
    def test_entry_thresholds_from_normal(self, soc: int, expected: PowerMode) -> None:
        assert evaluate_soc_mode(PowerMode.NORMAL, soc) == expected

    def test_hysteresis_stays_in_eco(self) -> None:
        # In ECO, 72 is above enter (70) but below exit (75) → stays ECO.
        assert evaluate_soc_mode(PowerMode.ECO, 72) == PowerMode.ECO

    def test_hysteresis_leaves_eco_at_exit(self) -> None:
        assert evaluate_soc_mode(PowerMode.ECO, 75) == PowerMode.NORMAL

    def test_hysteresis_stays_in_low(self) -> None:
        # In LOW, 52 is above enter (50) but below exit (55) → stays LOW.
        assert evaluate_soc_mode(PowerMode.LOW, 52) == PowerMode.LOW

    def test_hysteresis_stays_in_critical(self) -> None:
        # In CRITICAL, 27 is above enter (25) but below exit (30) → stays CRITICAL.
        assert evaluate_soc_mode(PowerMode.CRITICAL, 27) == PowerMode.CRITICAL

    def test_custom_thresholds(self) -> None:
        enter = {PowerMode.CRITICAL: 10, PowerMode.LOW: 30, PowerMode.ECO: 50}
        exit_ = {PowerMode.CRITICAL: 15, PowerMode.LOW: 35, PowerMode.ECO: 55}
        assert evaluate_soc_mode(PowerMode.NORMAL, 55, enter_at=enter, exit_at=exit_) == (
            PowerMode.NORMAL
        )
        assert evaluate_soc_mode(PowerMode.NORMAL, 49, enter_at=enter, exit_at=exit_) == (
            PowerMode.ECO
        )


class TestComputeSolarMode:
    def test_night_returns_no_escalation(self) -> None:
        mode, status = compute_solar_mode(
            is_daytime=False,
            soc_pct=50,
            net_avg_ma=None,
            hours_until_eod=0.0,
            battery_capacity_mah=1500,
            min_overnight_soc=30,
        )
        assert mode is None
        assert status.is_daytime is False

    def test_daytime_no_data_is_conservative(self) -> None:
        mode, status = compute_solar_mode(
            is_daytime=True,
            soc_pct=80,
            net_avg_ma=None,
            hours_until_eod=6.0,
            battery_capacity_mah=1500,
            min_overnight_soc=30,
        )
        assert mode == PowerMode.ECO
        assert status.mode_reason == "solar_no_data"

    def test_strong_charge_no_escalation(self) -> None:
        # Charging hard with hours of sun left → projected SoC clears reserve.
        mode, status = compute_solar_mode(
            is_daytime=True,
            soc_pct=70,
            net_avg_ma=500.0,
            hours_until_eod=5.0,
            battery_capacity_mah=1500,
            min_overnight_soc=30,
        )
        assert mode is None
        assert status.projected_eod_soc is not None
        assert status.deficit_pct is not None and status.deficit_pct < 0

    def test_net_drain_escalates(self) -> None:
        # Net discharge during the day with low SoC → big projected deficit.
        mode, status = compute_solar_mode(
            is_daytime=True,
            soc_pct=30,
            net_avg_ma=-100.0,
            hours_until_eod=4.0,
            battery_capacity_mah=1500,
            min_overnight_soc=30,
        )
        assert mode == PowerMode.CRITICAL
        assert status.deficit_pct is not None and status.deficit_pct > 25

    def test_projection_capped_at_full(self) -> None:
        mode, status = compute_solar_mode(
            is_daytime=True,
            soc_pct=95,
            net_avg_ma=1000.0,
            hours_until_eod=5.0,
            battery_capacity_mah=1500,
            min_overnight_soc=30,
        )
        assert mode is None
        assert status.projected_eod_soc == 100.0


class TestCombineModes:
    def test_soc_dominates(self) -> None:
        mode, reason = combine_modes(PowerMode.LOW, PowerMode.ECO, has_solar_data=True)
        assert mode == PowerMode.LOW
        assert reason == "soc"

    def test_solar_escalates(self) -> None:
        mode, reason = combine_modes(PowerMode.NORMAL, PowerMode.LOW, has_solar_data=True)
        assert mode == PowerMode.LOW
        assert reason == "solar"

    def test_solar_no_data_reason(self) -> None:
        mode, reason = combine_modes(PowerMode.NORMAL, PowerMode.ECO, has_solar_data=False)
        assert mode == PowerMode.ECO
        assert reason == "solar_no_data"

    def test_no_solar_mode(self) -> None:
        mode, reason = combine_modes(PowerMode.ECO, None, has_solar_data=False)
        assert mode == PowerMode.ECO
        assert reason == "soc"


class TestComputeDawnRecoveryMode:
    def test_no_dawn_soc_recorded_no_constraint(self) -> None:
        assert compute_dawn_recovery_mode(dawn_soc_pct=None, current_soc_pct=30) is None

    def test_healthy_dawn_soc_no_constraint(self) -> None:
        # Dawn at 60% ≥ threshold (50) → no recovery constraint
        assert compute_dawn_recovery_mode(dawn_soc_pct=60, current_soc_pct=40) is None

    def test_depleted_dawn_holds_low_until_recovery(self) -> None:
        # Dawn at 35% < threshold; current at 55% < recovery (65) → LOW
        assert compute_dawn_recovery_mode(dawn_soc_pct=35, current_soc_pct=55) == PowerMode.LOW

    def test_depleted_dawn_lifts_once_recovered(self) -> None:
        # Dawn at 35% < threshold; current at 65% ≥ recovery → no constraint
        assert compute_dawn_recovery_mode(dawn_soc_pct=35, current_soc_pct=65) is None

    def test_dawn_exactly_at_threshold_no_constraint(self) -> None:
        # dawn_soc_pct == dawn_low_threshold → "healthy" (not depleted)
        assert compute_dawn_recovery_mode(dawn_soc_pct=50, current_soc_pct=40) is None

    def test_custom_thresholds(self) -> None:
        mode = compute_dawn_recovery_mode(
            dawn_soc_pct=30,
            current_soc_pct=55,
            dawn_low_threshold=40,
            recovery_threshold=70,
        )
        assert mode == PowerMode.LOW
