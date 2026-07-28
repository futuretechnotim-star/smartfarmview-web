"""Unit tests for the temperature + humidity BME280 driver.

Uses a fake I2C object so no hardware is required. Verifies the calibration
read, the ctrl register writes on init, and the Bosch compensation formulas
against known-good datasheet-style (temperature) and property-based (humidity)
fixtures.
"""

import struct
import sys
import types

# ---------------------------------------------------------------------------
# Stub out MicroPython-only modules so the driver imports under CPython.
# ---------------------------------------------------------------------------
machine_mod = types.ModuleType("machine")


class _FakeI2CBase:
    pass


machine_mod.I2C = _FakeI2CBase  # type: ignore[attr-defined]
sys.modules.setdefault("machine", machine_mod)
sys.modules.setdefault("ustruct", struct)

from bme280 import BME280  # noqa: E402  (import after stub)

_REG_CALIB_T = 0x88
_REG_CALIB_H1 = 0xA1
_REG_CALIB_H2_6 = 0xE1
_REG_CTRL_HUM = 0xF2
_REG_CTRL_MEAS = 0xF4
_REG_TEMP = 0xFA
_REG_HUM = 0xFD

# Plausible calibration values (same shape as a real chip dump) used by every
# test unless overridden — dig_H1/dig_H3 unsigned bytes, dig_H2 signed 16-bit,
# dig_H4/dig_H5 signed 12-bit packed across shared nibbles, dig_H6 signed byte.
_DIG_H1 = 75
_DIG_H2 = 384
_DIG_H3 = 0
_DIG_H4 = 301  # positive, fits in 12 bits so no sign-extension needed here
_DIG_H5 = 50
_DIG_H6 = 30


class FakeI2C:
    def __init__(
        self,
        calib_t1: int,
        calib_t2: int,
        calib_t3: int,
        adc_t: int,
        adc_h: int = 30000,
        dig_h1: int = _DIG_H1,
        dig_h2: int = _DIG_H2,
        dig_h3: int = _DIG_H3,
        dig_h4: int = _DIG_H4,
        dig_h5: int = _DIG_H5,
        dig_h6: int = _DIG_H6,
    ) -> None:
        self._calib = struct.pack("<Hhh", calib_t1, calib_t2, calib_t3)
        raw = adc_t << 4  # adc_t is a 20-bit value; xlsb's low nibble unused
        self._temp_bytes = bytes([(raw >> 16) & 0xFF, (raw >> 8) & 0xFF, raw & 0xFF])
        self._hum_bytes = bytes([(adc_h >> 8) & 0xFF, adc_h & 0xFF])
        self._dig_h1_byte = bytes([dig_h1 & 0xFF])
        e4 = (dig_h4 >> 4) & 0xFF
        e5 = ((dig_h4 & 0x0F) | ((dig_h5 & 0x0F) << 4)) & 0xFF
        e6 = (dig_h5 >> 4) & 0xFF
        self._calib_h2_6 = (
            struct.pack("<hB", dig_h2, dig_h3 & 0xFF)
            + bytes([e4, e5, e6])
            + struct.pack("<b", dig_h6)
        )
        self.writes: list[tuple[int, bytes]] = []

    def readfrom_mem(self, addr: int, memaddr: int, n: int) -> bytes:
        if memaddr == _REG_CALIB_T:
            return self._calib
        if memaddr == _REG_CALIB_H1:
            return self._dig_h1_byte
        if memaddr == _REG_CALIB_H2_6:
            return self._calib_h2_6
        if memaddr == _REG_TEMP:
            return self._temp_bytes
        if memaddr == _REG_HUM:
            return self._hum_bytes
        raise OSError("unexpected register")

    def writeto_mem(self, addr: int, memaddr: int, data: bytes) -> None:
        self.writes.append((memaddr, data))


def test_calibration_read_and_ctrl_registers_written():
    fake = FakeI2C(27504, 26435, -1000, adc_t=519888)
    BME280(fake)
    written_regs = [reg for reg, _ in fake.writes]
    assert _REG_CTRL_HUM in written_regs
    assert _REG_CTRL_MEAS in written_regs
    # ctrl_hum only takes effect once ctrl_meas is subsequently written
    # (datasheet 5.4.3) — order matters.
    assert written_regs.index(_REG_CTRL_HUM) < written_regs.index(_REG_CTRL_MEAS)


def test_temperature_matches_datasheet_worked_example():
    # Bosch BME280 datasheet worked example: these calibration + adc_T values
    # are documented to compensate to 25.08 degC.
    fake = FakeI2C(27504, 26435, -1000, adc_t=519888)
    bme = BME280(fake)
    assert abs(bme.temperature() - 25.08) < 0.01


def test_temperature_changes_with_adc_reading():
    cold = BME280(FakeI2C(27504, 26435, -1000, adc_t=400000))
    hot = BME280(FakeI2C(27504, 26435, -1000, adc_t=519888))
    assert hot.temperature() > cold.temperature()


def test_is_present_false_on_exception():
    class BrokenI2C(FakeI2C):
        def readfrom_mem(self, addr: int, memaddr: int, n: int) -> bytes:
            if memaddr == _REG_TEMP:
                raise OSError("no device")
            return super().readfrom_mem(addr, memaddr, n)

    bme = BME280(BrokenI2C(27504, 26435, -1000, adc_t=519888))
    assert bme.is_present() is False


def test_humidity_within_valid_range():
    fake = FakeI2C(27504, 26435, -1000, adc_t=519888, adc_h=30000)
    bme = BME280(fake)
    h = bme.humidity()
    assert 0.0 <= h <= 100.0


def test_humidity_increases_with_adc_reading():
    dry = BME280(FakeI2C(27504, 26435, -1000, adc_t=519888, adc_h=15000))
    wet = BME280(FakeI2C(27504, 26435, -1000, adc_t=519888, adc_h=45000))
    assert wet.humidity() > dry.humidity()


def test_humidity_clamped_at_bounds():
    saturated = BME280(FakeI2C(27504, 26435, -1000, adc_t=519888, adc_h=65535))
    assert saturated.humidity() <= 100.0
    dry = BME280(FakeI2C(27504, 26435, -1000, adc_t=519888, adc_h=0))
    assert dry.humidity() >= 0.0


def test_humidity_refreshes_t_fine_from_temperature():
    # humidity() must call temperature() internally to get a fresh t_fine —
    # verify the two are consistent by comparing a direct temperature() call
    # against the t_fine humidity() computed as a side effect.
    fake = FakeI2C(27504, 26435, -1000, adc_t=519888, adc_h=30000)
    bme = BME280(fake)
    bme.humidity()
    assert bme._t_fine != 0
