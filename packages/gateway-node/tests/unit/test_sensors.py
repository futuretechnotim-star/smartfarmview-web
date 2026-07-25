"""Tests for the pure logic in gateway_node.sensors (GPS payload shaping and
fix-staleness decision) — the I2C polling loop itself is hardware-dependent
and exempt per this repo's TDD conventions."""

from gateway_node.gps_protocol import RTK_FIXED, RTK_NONE, GpsFix
from gateway_node.sensors import _gps_payload_fields, _is_fix_stale


def _fix(**overrides: object) -> GpsFix:
    defaults: dict[str, object] = dict(
        fix_type=3,
        num_satellites=32,
        latitude=41.85101871,
        longitude=-87.96789994,
        altitude_m=180.491,
        horizontal_accuracy_m=1.439,
        vertical_accuracy_m=2.358,
        rtk_status=RTK_NONE,
        pdop=0.98,
    )
    defaults.update(overrides)
    return GpsFix(**defaults)  # type: ignore[arg-type]


class TestGpsPayloadFields:
    def test_none_fix_publishes_all_none(self):
        payload = _gps_payload_fields(None)
        assert payload == {
            "gps_fix_type": None,
            "gps_num_satellites": None,
            "gps_lat": None,
            "gps_lon": None,
            "gps_altitude_m": None,
            "gps_h_acc_m": None,
            "gps_v_acc_m": None,
            "gps_rtk_status": None,
            "gps_pdop": None,
        }

    def test_valid_fix_rounds_and_labels_fields(self):
        payload = _gps_payload_fields(_fix())
        assert payload["gps_num_satellites"] == 32
        assert payload["gps_lat"] == round(41.85101871, 8)
        assert payload["gps_lon"] == round(-87.96789994, 8)
        assert payload["gps_altitude_m"] == round(180.491, 3)
        assert payload["gps_rtk_status"] == "none"

    def test_rtk_status_label_maps_from_enum(self):
        payload = _gps_payload_fields(_fix(rtk_status=RTK_FIXED))
        assert payload["gps_rtk_status"] == "fixed"


class TestIsFixStale:
    def test_fresh_fix_not_stale(self):
        assert _is_fix_stale(age_s=0.0, timeout_s=120.0) is False

    def test_within_timeout_not_stale(self):
        assert _is_fix_stale(age_s=119.9, timeout_s=120.0) is False

    def test_past_timeout_is_stale(self):
        assert _is_fix_stale(age_s=120.1, timeout_s=120.0) is True

    def test_exactly_at_timeout_not_yet_stale(self):
        assert _is_fix_stale(age_s=120.0, timeout_s=120.0) is False
