"""Tests for the pure UBX/ZED-F9P protocol parsing (gps_protocol.py)."""

from gateway_node.gps_protocol import (
    NAV_PVT_LENGTH,
    NAV_PVT_STRUCT,
    RTK_FIXED,
    RTK_FLOAT,
    RTK_NONE,
    UBX_CLASS_CFG,
    UBX_CLASS_NAV,
    UBX_NAV_PVT,
    UbxStreamParser,
    build_ubx_frame,
    parse_nav_pvt,
    verify_checksum,
)


def _nav_pvt_payload(
    *,
    fix_type: int = 3,
    flags: int = 0x01,
    num_sv: int = 24,
    lon_deg: float = -83.123456,
    lat_deg: float = 40.123456,
    height_mm: int = 250_000,
    h_acc_mm: int = 15,
    v_acc_mm: int = 20,
    pdop_scaled: int = 150,
) -> bytes:
    # NAV_PVT_STRUCT only covers the fields we care about (78 bytes); pad to
    # the real payload's full 92 bytes (trailing reserved/headVeh/magDec/
    # magAcc fields we don't parse) so parse_nav_pvt's length guard passes,
    # same as a real device payload would.
    packed = NAV_PVT_STRUCT.pack(
        0,  # iTOW
        2026,  # year
        7,  # month
        22,  # day
        10,  # hour
        0,  # min
        0,  # sec
        0x07,  # valid
        0,  # tAcc
        0,  # nano
        fix_type,
        flags,
        0,  # flags2
        num_sv,
        int(lon_deg / 1e-7),
        int(lat_deg / 1e-7),
        height_mm,
        height_mm,  # hMSL (unused by our parser)
        h_acc_mm,
        v_acc_mm,
        0, 0, 0, 0, 0,  # velN velE velD gSpeed headMot
        0,  # sAcc
        0,  # headAcc
        pdop_scaled,
    )
    return packed + bytes(NAV_PVT_LENGTH - len(packed))


class TestParseNavPvt:
    def test_too_short_returns_none(self):
        assert parse_nav_pvt(b"\x00" * 10) is None

    def test_extracts_position_and_accuracy(self):
        payload = _nav_pvt_payload(lat_deg=40.123456, lon_deg=-83.123456, height_mm=250_000, h_acc_mm=15)
        fix = parse_nav_pvt(payload)
        assert fix is not None
        assert round(fix.latitude, 6) == 40.123456
        assert round(fix.longitude, 6) == -83.123456
        assert round(fix.altitude_m, 3) == 250.0
        assert round(fix.horizontal_accuracy_m, 3) == 0.015

    def test_fix_type_and_num_satellites(self):
        fix = parse_nav_pvt(_nav_pvt_payload(fix_type=3, num_sv=24))
        assert fix.fix_type == 3
        assert fix.num_satellites == 24

    def test_rtk_status_none_when_carrsoln_bits_clear(self):
        fix = parse_nav_pvt(_nav_pvt_payload(flags=0x01))  # gnssFixOK only
        assert fix.rtk_status == RTK_NONE

    def test_rtk_status_float_from_carrsoln_bits(self):
        # carrSoln lives in bits 6-7 of the flags byte; 1 = float.
        fix = parse_nav_pvt(_nav_pvt_payload(flags=(1 << 6) | 0x01))
        assert fix.rtk_status == RTK_FLOAT

    def test_rtk_status_fixed_from_carrsoln_bits(self):
        fix = parse_nav_pvt(_nav_pvt_payload(flags=(2 << 6) | 0x01))
        assert fix.rtk_status == RTK_FIXED

    def test_pdop_scaling(self):
        fix = parse_nav_pvt(_nav_pvt_payload(pdop_scaled=150))
        assert round(fix.pdop, 2) == 1.50


class TestChecksumAndFraming:
    def test_build_then_verify_roundtrip(self):
        frame = build_ubx_frame(UBX_CLASS_CFG, 0x01, bytes([1, 2, 3]))
        assert verify_checksum(frame) is True

    def test_verify_rejects_corrupted_frame(self):
        frame = bytearray(build_ubx_frame(UBX_CLASS_CFG, 0x01, bytes([1, 2, 3])))
        frame[-1] ^= 0xFF  # flip the last checksum byte
        assert verify_checksum(bytes(frame)) is False

    def test_verify_rejects_too_short(self):
        assert verify_checksum(b"\x00" * 4) is False


class TestUbxStreamParser:
    def test_yields_one_complete_frame(self):
        parser = UbxStreamParser()
        frame = build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        frames = parser.feed(frame)
        assert len(frames) == 1
        msg_class, msg_id, payload = frames[0]
        assert msg_class == UBX_CLASS_NAV
        assert msg_id == UBX_NAV_PVT
        assert parse_nav_pvt(payload) is not None

    def test_skips_garbage_before_sync(self):
        parser = UbxStreamParser()
        frame = build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        garbage = b"$GPGGA,junk*ff\r\n"
        frames = parser.feed(garbage + frame)
        assert len(frames) == 1

    def test_waits_for_more_bytes_on_partial_frame(self):
        parser = UbxStreamParser()
        frame = build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        assert parser.feed(frame[:10]) == []
        frames = parser.feed(frame[10:])
        assert len(frames) == 1

    def test_drops_frame_with_bad_checksum(self):
        parser = UbxStreamParser()
        frame = bytearray(build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload()))
        frame[-1] ^= 0xFF
        assert parser.feed(bytes(frame)) == []

    def test_two_back_to_back_frames(self):
        parser = UbxStreamParser()
        frame = build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        frames = parser.feed(frame + frame)
        assert len(frames) == 2
