"""Tests for the pure UBX/ZED-F9P protocol parsing (gps_protocol.py)."""

import struct

from gateway_node.gps_protocol import (
    CFG_I2COUTPROT_NMEA,
    CFG_I2COUTPROT_RTCM3X,
    CFG_LAYER_BBR,
    CFG_LAYER_FLASH,
    CFG_LAYER_RAM,
    CFG_MSGOUT_UBX_NAV_PVT_I2C,
    NAV_PVT_LENGTH,
    NAV_PVT_STRUCT,
    NAV_SVIN_LENGTH,
    NAV_SVIN_STRUCT,
    RTK_FIXED,
    RTK_FLOAT,
    RTK_NONE,
    UBX_ACK_ACK,
    UBX_ACK_NAK,
    UBX_CFG_VALSET,
    UBX_CLASS_ACK,
    UBX_CLASS_CFG,
    UBX_CLASS_NAV,
    UBX_NAV_PVT,
    UBX_SYNC_CHAR_1,
    UBX_SYNC_CHAR_2,
    UbxStreamParser,
    build_cfg_valset,
    build_ubx_frame,
    find_ack,
    parse_nav_pvt,
    parse_nav_svin,
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
        0,
        0,
        0,
        0,
        0,  # velN velE velD gSpeed headMot
        0,  # sAcc
        0,  # headAcc
        pdop_scaled,
    )
    return packed + bytes(NAV_PVT_LENGTH - len(packed))


class TestParseNavPvt:
    def test_too_short_returns_none(self):
        assert parse_nav_pvt(b"\x00" * 10) is None

    def test_extracts_position_and_accuracy(self):
        payload = _nav_pvt_payload(
            lat_deg=40.123456, lon_deg=-83.123456, height_mm=250_000, h_acc_mm=15
        )
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


def _nav_svin_payload(
    *,
    dur_s: int = 120,
    mean_acc_mm_tenths: int = 15000,  # 1.5 m in 0.1mm units
    valid: int = 0,
    active: int = 1,
) -> bytes:
    packed = NAV_SVIN_STRUCT.pack(
        0,  # version
        b"\x00\x00\x00",  # reserved1
        0,  # iTOW
        dur_s,
        0,
        0,
        0,  # meanX meanY meanZ
        0,
        0,
        0,  # meanXHP meanYHP meanZHP
        0,  # reserved2
        mean_acc_mm_tenths,
        0,  # obs
        valid,
        active,
        b"\x00\x00",  # reserved3
    )
    return packed + bytes(NAV_SVIN_LENGTH - len(packed))


class TestParseNavSvin:
    def test_too_short_returns_none(self):
        assert parse_nav_svin(b"\x00" * 10) is None

    def test_extracts_duration_and_accuracy(self):
        svin = parse_nav_svin(_nav_svin_payload(dur_s=180, mean_acc_mm_tenths=25000))
        assert svin is not None
        assert svin.dur_s == 180
        assert round(svin.mean_acc_m, 3) == 2.5

    def test_not_valid_while_surveying(self):
        svin = parse_nav_svin(_nav_svin_payload(valid=0, active=1))
        assert svin.valid is False
        assert svin.active is True

    def test_valid_once_survey_completes(self):
        svin = parse_nav_svin(_nav_svin_payload(valid=1, active=0))
        assert svin.valid is True
        assert svin.active is False


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

    def test_recovers_from_bogus_large_length(self):
        # A spurious sync followed by a huge length field (0xFFFF) must NOT
        # block the real frame behind it. Before the resync guard this
        # deadlocked the parser for the life of the process.
        parser = UbxStreamParser()
        bogus = bytes([UBX_SYNC_CHAR_1, UBX_SYNC_CHAR_2, 0x99, 0x99, 0xFF, 0xFF])
        good = build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        frames = parser.feed(bogus + good)
        assert len(frames) == 1
        assert (frames[0][0], frames[0][1]) == (UBX_CLASS_NAV, UBX_NAV_PVT)

    def test_recovers_from_false_sync_with_bad_checksum(self):
        # A false sync with a small, plausible length whose frame fails the
        # checksum must resync (skip a byte) and still find the real frame,
        # rather than discarding the whole claimed span.
        parser = UbxStreamParser()
        false = bytes(
            [UBX_SYNC_CHAR_1, UBX_SYNC_CHAR_2, 0x01, 0x02, 0x03, 0x00, 0xAA, 0xBB, 0xCC, 0x00, 0x00]
        )
        good = build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        frames = parser.feed(false + good)
        assert len(frames) == 1


class TestBuildCfgValset:
    def test_encodes_header_keys_and_values(self):
        frame = build_cfg_valset(
            [(CFG_I2COUTPROT_NMEA, 0), (CFG_MSGOUT_UBX_NAV_PVT_I2C, 1)],
            CFG_LAYER_RAM | CFG_LAYER_BBR | CFG_LAYER_FLASH,
        )
        assert verify_checksum(frame)
        ((cls, mid, payload),) = UbxStreamParser().feed(frame)
        assert (cls, mid) == (UBX_CLASS_CFG, UBX_CFG_VALSET)
        assert payload[0] == 0x00  # version
        assert payload[1] == 0x07  # layers: RAM | BBR | Flash
        assert payload[2:4] == b"\x00\x00"  # reserved
        assert payload[4:8] == struct.pack("<I", CFG_I2COUTPROT_NMEA)
        assert payload[8] == 0
        assert payload[9:13] == struct.pack("<I", CFG_MSGOUT_UBX_NAV_PVT_I2C)
        assert payload[13] == 1

    def test_ram_only_layer_byte(self):
        frame = build_cfg_valset([(CFG_I2COUTPROT_RTCM3X, 0)], CFG_LAYER_RAM)
        ((_, _, payload),) = UbxStreamParser().feed(frame)
        assert payload[1] == 0x01


def _ack_frame(is_ack: bool, cls: int, mid: int) -> bytes:
    return build_ubx_frame(UBX_CLASS_ACK, UBX_ACK_ACK if is_ack else UBX_ACK_NAK, bytes([cls, mid]))


class TestFindAck:
    def test_returns_true_on_ack(self):
        frames = UbxStreamParser().feed(_ack_frame(True, UBX_CLASS_CFG, UBX_CFG_VALSET))
        assert find_ack(frames, UBX_CLASS_CFG, UBX_CFG_VALSET) is True

    def test_returns_false_on_nak(self):
        frames = UbxStreamParser().feed(_ack_frame(False, UBX_CLASS_CFG, UBX_CFG_VALSET))
        assert find_ack(frames, UBX_CLASS_CFG, UBX_CFG_VALSET) is False

    def test_returns_none_when_no_ack_present(self):
        frames = UbxStreamParser().feed(
            build_ubx_frame(UBX_CLASS_NAV, UBX_NAV_PVT, _nav_pvt_payload())
        )
        assert find_ack(frames, UBX_CLASS_CFG, UBX_CFG_VALSET) is None

    def test_ignores_ack_for_a_different_message(self):
        frames = UbxStreamParser().feed(_ack_frame(True, UBX_CLASS_CFG, 0x01))
        assert find_ack(frames, UBX_CLASS_CFG, UBX_CFG_VALSET) is None
