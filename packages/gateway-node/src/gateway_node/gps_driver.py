"""ZED-F9P GPS-RTK2 driver over I2C (DDC) — hardware glue around gps_protocol.

Synchronous, single-poll-cycle usage (unlike the landplan-survey-stick's
asyncio version): gateway_node/sensors.py calls drain_latest_fix() once per
polling tick to get whatever fix accumulated since the last call.

Configuration (see _configure): a single UBX-CFG-VALSET at construction
(a) enables NAV-PVT output on I2C at 1 Hz and (b) disables NMEA and RTCM3
output on the I2C port. That second part is the fix for the long-standing
"GPS goes silent on DDC" intermittency: with UBX + NMEA + RTCM3 all emitted on
I2C, the module's small DDC TX buffer overruns (MON-COMMS shows it peaking at
100%) and its periodic-message output wedges, while on-demand poll responses
still work — which is exactly why it looked like a random dropout. Disabling
NMEA/RTCM3 on I2C makes NAV-PVT stream continuously (validated: 240 s straight,
zero dropouts, no reconfigures). See docs/gpsrtk.md.

NOTE: this disables RTCM3 *on the I2C port only*. This gateway is intended as
an RTK base station, but the correction-sharing path to the rover (survey
stick) is not built yet, and nothing currently consumes RTCM3 over I2C. When
that path is designed, corrections must go out a port that isn't fighting
NAV-PVT for the DDC buffer (UART is the natural choice) — revisit this then.

The startup config is written to RAM + BBR + Flash so it persists across
reboots (previously it lived in RAM only and reverted to the module default —
NAV-PVT-off — on every power cycle). The self-heal reconfigures below write
RAM only, to avoid flash wear on repeated recovery attempts.

Reconfigures automatically after an I2C error rather than raising — a GPS
hiccup shouldn't take down the whole sensors service — and also if no NAV-PVT
frame has parsed in over _STALE_FRAME_TIMEOUT_S even without an I2C error.
Both write RAM only. With the buffer-overload cause removed these should
rarely fire, but they remain as cheap safety nets.

Hardware-dependent (I2C) — exempt from unit tests per this repo's TDD
conventions; the parsing/encoding logic it calls into (gps_protocol.py) is
pure and fully tested.
"""

from __future__ import annotations

import time

import smbus2
import structlog

from gateway_node.gps_protocol import (
    CFG_I2COUTPROT_NMEA,
    CFG_I2COUTPROT_RTCM3X,
    CFG_LAYER_BBR,
    CFG_LAYER_FLASH,
    CFG_LAYER_RAM,
    CFG_MSGOUT_UBX_NAV_PVT_I2C,
    REG_BYTES_AVAIL_HIGH,
    REG_BYTES_AVAIL_LOW,
    REG_DATA_STREAM,
    UBX_CFG_VALSET,
    UBX_CLASS_CFG,
    UBX_CLASS_NAV,
    UBX_NAV_PVT,
    ZED_F9P_I2C_ADDR,
    GpsFix,
    UbxStreamParser,
    build_cfg_valset,
    find_ack,
    parse_nav_pvt,
)

log = structlog.get_logger()

_READ_CHUNK = 32  # SMBus hard limit per read_i2c_block_data call
_STALE_FRAME_TIMEOUT_S = 120  # no NAV-PVT frame parsed for this long -> resend config
_ACK_TIMEOUT_S = 1.0  # how long to wait for CFG-VALSET's UBX-ACK

# I2C output: UBX NAV-PVT on at 1 Hz, NMEA + RTCM3 off (see module docstring).
_CONFIG_ITEMS = [
    (CFG_I2COUTPROT_NMEA, 0),
    (CFG_I2COUTPROT_RTCM3X, 0),
    (CFG_MSGOUT_UBX_NAV_PVT_I2C, 1),
]


class ZedF9pDriver:
    def __init__(self, bus: smbus2.SMBus, addr: int = ZED_F9P_I2C_ADDR) -> None:
        self._bus = bus
        self._addr = addr
        self._parser = UbxStreamParser()
        self._configure(persist=True)
        self._last_frame_monotonic = time.monotonic()

    def _configure(self, persist: bool = False) -> None:
        """Send CFG-VALSET enabling NAV-PVT and disabling NMEA/RTCM3 on I2C.

        persist=True writes RAM + BBR + Flash (startup, survives reboot);
        persist=False writes RAM only (self-heal — no flash wear)."""
        layers = CFG_LAYER_RAM
        if persist:
            layers |= CFG_LAYER_BBR | CFG_LAYER_FLASH
        self._write_raw(build_cfg_valset(_CONFIG_ITEMS, layers))
        acked = self._read_ack(UBX_CLASS_CFG, UBX_CFG_VALSET, _ACK_TIMEOUT_S)
        if acked is True:
            log.info("zed_f9p_configured", addr=hex(self._addr), persist=persist)
        elif acked is False:
            log.warning("zed_f9p_config_nak", addr=hex(self._addr), persist=persist)
        else:
            log.warning("zed_f9p_config_no_ack", addr=hex(self._addr), persist=persist)

    def _write_raw(self, data: bytes) -> None:
        """Raw I2C write with no register byte (UBX config frames aren't
        addressed to a register — smbus2's write_i2c_block_data always
        prepends one, so this goes through i2c_msg/i2c_rdwr instead)."""
        msg = smbus2.i2c_msg.write(self._addr, data)
        self._bus.i2c_rdwr(msg)

    def _bytes_available(self) -> int:
        hi = self._bus.read_byte_data(self._addr, REG_BYTES_AVAIL_HIGH)
        lo = self._bus.read_byte_data(self._addr, REG_BYTES_AVAIL_LOW)
        return (hi << 8) | lo

    def _read_chunk(self) -> bytes:
        n = self._bytes_available()
        if n <= 0:
            return b""
        return bytes(
            self._bus.read_i2c_block_data(self._addr, REG_DATA_STREAM, min(n, _READ_CHUNK))
        )

    def _read_ack(self, msg_class: int, msg_id: int, timeout_s: float) -> bool | None:
        """Drain the DDC until an ACK-ACK/ACK-NAK for (msg_class, msg_id)
        arrives or timeout_s elapses. Returns True/False/None (no ACK seen).
        Any NAV-PVT frames that arrive in this brief window are discarded."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = self._read_chunk()
            if raw:
                acked = find_ack(self._parser.feed(raw), msg_class, msg_id)
                if acked is not None:
                    return acked
            else:
                time.sleep(0.02)
        return None

    def drain_latest_fix(self) -> GpsFix | None:
        """Read whatever's accumulated in the module's output buffer since
        the last call and return the most recent parsed fix (None if
        nothing new arrived, or on error)."""
        latest: GpsFix | None = None
        try:
            while True:
                raw = self._read_chunk()
                if not raw:
                    break
                for msg_class, msg_id, payload in self._parser.feed(raw):
                    if msg_class == UBX_CLASS_NAV and msg_id == UBX_NAV_PVT:
                        fix = parse_nav_pvt(payload)
                        if fix is not None:
                            latest = fix
        except OSError as e:
            log.warning("gps_i2c_error_reconfiguring", error=str(e))
            time.sleep(0.5)
            try:
                self._configure()
            except OSError as e2:
                log.warning("gps_reconfigure_failed", error=str(e2))
            self._last_frame_monotonic = time.monotonic()
            return latest

        if latest is not None:
            self._last_frame_monotonic = time.monotonic()
        elif time.monotonic() - self._last_frame_monotonic >= _STALE_FRAME_TIMEOUT_S:
            log.warning(
                "gps_no_frames_reconfiguring",
                idle_s=round(time.monotonic() - self._last_frame_monotonic, 1),
            )
            try:
                self._configure()
            except OSError as e:
                log.warning("gps_reconfigure_failed", error=str(e))
            self._last_frame_monotonic = time.monotonic()

        return latest
