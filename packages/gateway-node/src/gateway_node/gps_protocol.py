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
UBX_CLASS_CFG = 0x06

UBX_NAV_PVT = 0x07
UBX_CFG_MSG = 0x01

ZED_F9P_I2C_ADDR = 0x42
REG_BYTES_AVAIL_HIGH = 0xFD
REG_BYTES_AVAIL_LOW = 0xFE
REG_DATA_STREAM = 0xFF

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


def build_ubx_frame(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    """Build a complete UBX frame (sync + header + payload + checksum) ready
    to write raw to the module (no register byte — see gps_driver.py)."""
    length = len(payload)
    frame = (
        bytes([UBX_SYNC_CHAR_1, UBX_SYNC_CHAR_2, msg_class, msg_id, length & 0xFF, (length >> 8) & 0xFF])
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
            msg_class = self._buf[2]
            msg_id = self._buf[3]
            length = struct.unpack_from("<H", self._buf, 4)[0]
            total = 6 + length + 2
            if len(self._buf) < total:
                break  # wait for more bytes
            frame = bytes(self._buf[:total])
            if verify_checksum(frame):
                frames.append((msg_class, msg_id, frame[6 : 6 + length]))
            del self._buf[:total]
        return frames
