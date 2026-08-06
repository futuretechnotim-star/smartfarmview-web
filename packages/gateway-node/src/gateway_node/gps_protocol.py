"""Pure UBX protocol parsing for the ZED-F9P GPS-RTK2 module (u-blox binary
protocol, I2C/DDC transport).

No I2C/hardware imports — the hardware glue (register reads, stream
draining, reconfiguring on error) lives in ``gps_driver.py``. Ported from
the landplan-survey-stick project's ublox driver (same NAV-PVT layout,
same 1 Hz DDC config sequence), adapted for a synchronous polling service
instead of asyncio, and extended to actually derive RTK status (float/
fixed) from the NAV-PVT flags byte — the survey-stick's own driver leaves
this stubbed at 0 (see its docs/current_iot_architecture.md, "RTK
Corrections (Phase 2)").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

UBX_SYNC_CHAR_1 = 0xB5
UBX_SYNC_CHAR_2 = 0x62

UBX_CLASS_NAV = 0x01
UBX_CLASS_ACK = 0x05
UBX_CLASS_CFG = 0x06

UBX_NAV_PVT = 0x07
UBX_NAV_SVIN = 0x3B
UBX_CFG_MSG = 0x01  # legacy CFG-MSG (superseded by CFG-VALSET below)
UBX_CFG_VALSET = 0x8A
UBX_ACK_NAK = 0x00
UBX_ACK_ACK = 0x01

ZED_F9P_I2C_ADDR = 0x42
REG_BYTES_AVAIL_HIGH = 0xFD
REG_BYTES_AVAIL_LOW = 0xFE
REG_DATA_STREAM = 0xFF

# ── u-blox configuration interface (CFG-VALSET / CFG-VALGET) ────────────────
# CFG-VALSET writes config items into one or more "layers". RAM is live-only
# (lost on power cycle); BBR (battery-backed RAM) and Flash persist. We write
# all three at startup so the config survives reboots without depending on the
# service re-sending it — see gps_driver._configure.
CFG_LAYER_RAM = 0x01
CFG_LAYER_BBR = 0x02
CFG_LAYER_FLASH = 0x04

# Config key IDs. The size bits (28-30) encode each value's width — see
# _cfg_value_size. I2COUTPROT-* gate which protocols the module emits on the
# I2C/DDC port; leaving NMEA and RTCM3 enabled there floods the small DDC TX
# buffer (it peaks at 100% and the periodic-message output wedges), which was
# the root cause of the "GPS goes silent on DDC" intermittency. We consume
# only UBX NAV-PVT, so we disable the other two on I2C output.
CFG_I2COUTPROT_UBX = 0x10720001
CFG_I2COUTPROT_NMEA = 0x10720002
CFG_I2COUTPROT_RTCM3X = 0x10720004
CFG_MSGOUT_UBX_NAV_PVT_I2C = 0x20910006

# RTK base-station (survey-in / TMODE3) keys — see gps_base_mode.py for the
# state machine that sequences these. Values verified against two independent
# open-source u-blox key-ID tables (gpsd's ubxtool.in and phkehl/ubloxcfg's
# ubloxcfg_gen.c, both agreeing, and cross-checked against the already-live
# CFG_MSGOUT_UBX_NAV_PVT_I2C key above as a sanity anchor) — not hand-guessed.
# Still worth a live ACK check on first flash, same as any other CFG-VALSET.
CFG_TMODE_MODE = 0x20030001  # E1: 0=disabled 1=survey-in 2=fixed
CFG_TMODE_SVIN_MIN_DUR = 0x40030010  # U4, seconds
CFG_TMODE_SVIN_ACC_LIMIT = 0x40030011  # U4, 0.1mm units
CFG_MSGOUT_UBX_NAV_SVIN_I2C = 0x20910088
CFG_MSGOUT_RTCM_1005_I2C = 0x209102BD  # station ARP (antenna reference point)
CFG_MSGOUT_RTCM_1077_I2C = 0x209102CC  # GPS MSM7
CFG_MSGOUT_RTCM_1230_I2C = 0x20910303  # GLONASS code-phase biases

TMODE_DISABLED = 0
TMODE_SURVEY_IN = 1

# Largest UBX payload this driver will wait to complete before treating a sync
# match as spurious and resyncing. No message we read (NAV-PVT is 92 B, ACK is
# 2 B) comes close; anything larger is a torn read or a false 0xB5 0x62 in
# noise. Raise this only if a genuinely larger UBX message is ever enabled.
_MAX_UBX_PAYLOAD = 1024

NAV_PVT_LENGTH = 92
# Little-endian: iTOW,year,month,day,hour,min,sec,valid,tAcc,nano,fixType,
# flags,flags2,numSV,lon,lat,height,hMSL,hAcc,vAcc,velN,velE,velD,gSpeed,
# headMot,sAcc,headAcc,pDOP — per the u-blox ZED-F9P interface description.
NAV_PVT_STRUCT = struct.Struct("<IHBBBBBBIiBBBBiiiiIIiiiiiIIH")

# NAV-PVT fixType (offset 20) — no RTK distinction on its own.
FIX_NO_FIX = 0
FIX_DEAD_RECKONING = 1
FIX_2D = 2
FIX_3D = 3
FIX_GNSS_DEAD_RECKONING = 4
FIX_TIME_ONLY = 5

# carrSoln — flags bits 6-7 (offset 21). This IS the RTK status.
RTK_NONE = 0
RTK_FLOAT = 1
RTK_FIXED = 2


@dataclass
class GpsFix:
    fix_type: int
    num_satellites: int
    latitude: float
    longitude: float
    altitude_m: float  # height above the WGS84 ellipsoid
    horizontal_accuracy_m: float
    vertical_accuracy_m: float
    rtk_status: int  # RTK_NONE / RTK_FLOAT / RTK_FIXED
    pdop: float


NAV_SVIN_LENGTH = 40
# Little-endian: version,reserved1[3],iTOW,dur,meanX,meanY,meanZ,meanXHP,
# meanYHP,meanZHP,reserved2,meanAcc,obs,valid,active,reserved3[2] — per the
# u-blox ZED-F9P interface description. We only surface dur/meanAcc/valid/
# active; the mean position fields are the module's own internal survey
# result and aren't needed here (see gps_base_mode.py's BASE_ACTIVE note).
NAV_SVIN_STRUCT = struct.Struct("<B3sIIiiibbbBIIBB2s")


@dataclass
class SvinStatus:
    dur_s: int
    mean_acc_m: float
    valid: bool
    active: bool


def build_ubx_frame(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    """Build a complete UBX frame (sync + header + payload + checksum) ready
    to write raw to the module (no register byte — see gps_driver.py)."""
    length = len(payload)
    frame = (
        bytes(
            [
                UBX_SYNC_CHAR_1,
                UBX_SYNC_CHAR_2,
                msg_class,
                msg_id,
                length & 0xFF,
                (length >> 8) & 0xFF,
            ]
        )
        + payload
    )
    ck_a, ck_b = 0, 0
    for byte in frame[2:]:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return frame + bytes([ck_a, ck_b])


def verify_checksum(frame: bytes) -> bool:
    """Verify the UBX 8-bit Fletcher checksum over frame[2:-2]."""
    if len(frame) < 8:
        return False
    ck_a, ck_b = 0, 0
    for byte in frame[2:-2]:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a == frame[-2] and ck_b == frame[-1]


_CFG_KEY_SIZE_BITS = {0x1: 1, 0x2: 1, 0x3: 2, 0x4: 4, 0x5: 8}


def _cfg_value_size(key: int) -> int:
    """Value width in bytes for a CFG key, from its storage-size bits (28-30)."""
    return _CFG_KEY_SIZE_BITS[(key >> 28) & 0x7]


def build_cfg_valset(items: list[tuple[int, int]], layers: int) -> bytes:
    """Build a UBX-CFG-VALSET frame setting each ``(key, value)`` into the
    given config ``layers`` (an OR of ``CFG_LAYER_*``). Each value's width is
    derived from its key per the u-blox configuration interface."""
    body = bytearray([0x00, layers & 0xFF, 0x00, 0x00])  # version, layers, reserved[2]
    for key, value in items:
        body += struct.pack("<I", key)
        body += int(value).to_bytes(_cfg_value_size(key), "little")
    return build_ubx_frame(UBX_CLASS_CFG, UBX_CFG_VALSET, bytes(body))


def find_ack(frames: list[tuple[int, int, bytes]], msg_class: int, msg_id: int) -> bool | None:
    """Scan parsed UBX frames for an ACK-ACK / ACK-NAK acknowledging the
    message ``(msg_class, msg_id)``. Returns True on ACK, False on NAK, or
    None if neither is present (config writes are otherwise fire-and-forget)."""
    for cls, mid, payload in frames:
        if (
            cls == UBX_CLASS_ACK
            and len(payload) >= 2
            and payload[0] == msg_class
            and payload[1] == msg_id
        ):
            return mid == UBX_ACK_ACK
    return None


def parse_nav_pvt(payload: bytes) -> GpsFix | None:
    """Parse a NAV-PVT payload. Returns None if the payload is too short."""
    if len(payload) < NAV_PVT_LENGTH:
        return None
    f = NAV_PVT_STRUCT.unpack_from(payload[: NAV_PVT_STRUCT.size])
    # Tuple indices: 10=fixType 11=flags 13=numSV 14=lon 15=lat 16=height
    # 18=hAcc 19=vAcc 27=pDOP
    flags = f[11]
    carr_soln = (flags >> 6) & 0x3
    return GpsFix(
        fix_type=f[10],
        num_satellites=f[13],
        longitude=f[14] * 1e-7,
        latitude=f[15] * 1e-7,
        altitude_m=f[16] * 1e-3,
        horizontal_accuracy_m=f[18] * 1e-3,
        vertical_accuracy_m=f[19] * 1e-3,
        rtk_status=carr_soln,
        pdop=f[27] * 0.01,
    )


def parse_nav_svin(payload: bytes) -> SvinStatus | None:
    """Parse a NAV-SVIN payload. Returns None if the payload is too short."""
    if len(payload) < NAV_SVIN_LENGTH:
        return None
    f = NAV_SVIN_STRUCT.unpack_from(payload[: NAV_SVIN_STRUCT.size])
    # Tuple indices: 3=dur 11=meanAcc 13=valid 14=active
    return SvinStatus(
        dur_s=f[3],
        mean_acc_m=f[11] * 1e-4,
        valid=bool(f[13]),
        active=bool(f[14]),
    )


class UbxStreamParser:
    """Stateful byte-stream parser. feed() accumulates raw bytes read off the
    module and returns complete, checksum-verified UBX frames as they
    complete. Non-UBX bytes (e.g. NMEA sentences interleaved on the same
    port) are silently discarded byte-by-byte until sync is found again."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        self._buf.extend(data)
        frames: list[tuple[int, int, bytes]] = []
        while len(self._buf) >= 8:
            if self._buf[0] != UBX_SYNC_CHAR_1 or self._buf[1] != UBX_SYNC_CHAR_2:
                del self._buf[0]
                continue
            length = struct.unpack_from("<H", self._buf, 4)[0]
            if length > _MAX_UBX_PAYLOAD:
                # Implausible length: this "sync" is spurious (a torn read, or
                # a 0xB5 0x62 that fell inside noise). Skip one byte and resync
                # instead of waiting forever for a frame that never completes —
                # the head-of-line stall that used to wedge the parser for the
                # life of the process.
                del self._buf[0]
                continue
            total = 6 + length + 2
            if len(self._buf) < total:
                break  # genuine partial frame — wait for more bytes
            frame = bytes(self._buf[:total])
            if verify_checksum(frame):
                frames.append((self._buf[2], self._buf[3], frame[6 : 6 + length]))
                del self._buf[:total]
            else:
                # Checksum failed: treat the sync as spurious and resync from
                # the next byte, rather than discarding all `total` bytes which
                # could swallow a real frame starting inside this range.
                del self._buf[0]
        return frames
