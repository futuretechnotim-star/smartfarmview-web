"""ZED-F9P GPS-RTK2 driver over I2C (DDC) — hardware glue around gps_protocol.

Synchronous, single-poll-cycle usage (unlike the landplan-survey-stick's
asyncio version): gateway_node/sensors.py calls drain_latest_fix() once per
polling tick to get whatever fix accumulated since the last call. Configures
the module once at construction to output NAV-PVT on I2C at 1 Hz, and
reconfigures automatically after an I2C error rather than raising — a GPS
hiccup shouldn't take down the whole sensors service.

Hardware-dependent (I2C) — exempt from unit tests per this repo's TDD
conventions; the parsing logic it calls into (gps_protocol.py) is pure and
fully tested.
"""

from __future__ import annotations

import time

import smbus2  # type: ignore[import-not-found]
import structlog

from gateway_node.gps_protocol import (
    REG_BYTES_AVAIL_HIGH,
    REG_BYTES_AVAIL_LOW,
    REG_DATA_STREAM,
    UBX_CFG_MSG,
    UBX_CLASS_CFG,
    UBX_CLASS_NAV,
    UBX_NAV_PVT,
    ZED_F9P_I2C_ADDR,
    GpsFix,
    UbxStreamParser,
    build_ubx_frame,
    parse_nav_pvt,
)

log = structlog.get_logger()

_READ_CHUNK = 32  # SMBus hard limit per read_i2c_block_data call


class ZedF9pDriver:
    def __init__(self, bus: smbus2.SMBus, addr: int = ZED_F9P_I2C_ADDR) -> None:
        self._bus = bus
        self._addr = addr
        self._parser = UbxStreamParser()
        self._configure()

    def _configure(self) -> None:
        # CFG-MSG payload: [msgClass, msgId, rate_ddc, rate_uart1, rate_uart2, rate_usb, rate_spi, 0]
        payload = bytes([UBX_CLASS_NAV, UBX_NAV_PVT, 1, 0, 0, 0, 0, 0])
        frame = build_ubx_frame(UBX_CLASS_CFG, UBX_CFG_MSG, payload)
        self._write_raw(frame)
        time.sleep(0.1)  # give the module time to apply config and start outputting
        log.info("zed_f9p_configured", addr=hex(self._addr))

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

    def drain_latest_fix(self) -> GpsFix | None:
        """Read whatever's accumulated in the module's output buffer since
        the last call and return the most recent parsed fix (None if
        nothing new arrived, or on error)."""
        latest: GpsFix | None = None
        try:
            n = self._bytes_available()
            while n > 0:
                chunk = min(n, _READ_CHUNK)
                raw = bytes(self._bus.read_i2c_block_data(self._addr, REG_DATA_STREAM, chunk))
                for msg_class, msg_id, payload in self._parser.feed(raw):
                    if msg_class == UBX_CLASS_NAV and msg_id == UBX_NAV_PVT:
                        fix = parse_nav_pvt(payload)
                        if fix is not None:
                            latest = fix
                n = self._bytes_available()
        except OSError as e:
            log.warning("gps_i2c_error_reconfiguring", error=str(e))
            time.sleep(0.5)
            try:
                self._configure()
            except OSError as e2:
                log.warning("gps_reconfigure_failed", error=str(e2))
        return latest
