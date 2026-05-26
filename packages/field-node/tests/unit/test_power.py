from field_node.power.base import PowerReading, _voltage_to_soc


class TestVoltagToSoc:
    def test_full_charge(self) -> None:
        assert _voltage_to_soc(4.20) == 100

    def test_above_max_clamps_to_100(self) -> None:
        assert _voltage_to_soc(4.25) == 100

    def test_empty(self) -> None:
        assert _voltage_to_soc(3.30) == 0

    def test_below_min_clamps_to_0(self) -> None:
        assert _voltage_to_soc(3.0) == 0

    def test_midpoint_interpolation(self) -> None:
        # Midpoint between 4.20 (100%) and 4.15 (95%) → ~97-98%
        soc = _voltage_to_soc(4.175)
        assert 96 <= soc <= 98

    def test_known_point(self) -> None:
        assert _voltage_to_soc(4.02) == 80


class TestPowerReading:
    def test_is_discharging_when_current_negative(self) -> None:
        r = PowerReading(voltage_v=4.0, current_ma=-60.0, power_mw=240.0)
        assert r.is_discharging is True

    def test_not_discharging_when_charging(self) -> None:
        r = PowerReading(voltage_v=4.1, current_ma=150.0, power_mw=600.0)
        assert r.is_discharging is False

    def test_soc_pct_near_full(self) -> None:
        r = PowerReading(voltage_v=4.0, current_ma=-60.0, power_mw=240.0)
        assert 78 <= r.soc_pct <= 82

    def test_runtime_hours_discharging(self) -> None:
        r = PowerReading(voltage_v=4.0, current_ma=-60.0, power_mw=240.0)
        hours = r.runtime_hours(capacity_mah=1500)
        assert hours is not None
        # ~80% of 1500mAh / 60mA ≈ 20h
        assert 15.0 <= hours <= 25.0

    def test_runtime_hours_none_when_charging(self) -> None:
        r = PowerReading(voltage_v=4.1, current_ma=150.0, power_mw=600.0)
        assert r.runtime_hours(capacity_mah=1500) is None

    def test_runtime_hours_none_when_current_near_zero(self) -> None:
        r = PowerReading(voltage_v=4.0, current_ma=-0.5, power_mw=2.0)
        assert r.runtime_hours(capacity_mah=1500) is None
