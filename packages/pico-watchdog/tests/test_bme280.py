"""Unit tests for the temperature-only BME280 driver.

Uses a fake I2C object so no hardware is required. Verifies the calibration
read, the ctrl register writes on init, and the Bosch compensation formula
against known-good datasheet-style fixtures.
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
_REG_CTRL_HUM = 0xF2
_REG_CTRL_MEAS = 0xF4
_REG_TEMP = 0xFA


class FakeI2C:
    def __init__(self, calib_t1: int, calib_t2: int, calib_t3: int, adc_t: int) -> None:
        self._calib = struct.pack("<Hhh", calib_t1, calib_t2, calib_t3)
        raw = adc_t << 4  # adc_t is a 20-bit value; xlsb's low nibble unused
        self._temp_bytes = bytes([(raw >> 16) & 0xFF, (raw >> 8) & 0xFF, raw & 0xFF])
        self.writes: list[tuple[int, bytes]] = []

    def readfrom_mem(self, addr: int, memaddr: int, n: int) -> bytes:
        if memaddr == _REG_CALIB_T:
            return self._calib
        if memaddr == _REG_TEMP:
            return self._temp_bytes
        raise OSError("unexpected register")

    def writeto_mem(self, addr: int, memaddr: int, data: bytes) -> None:
        self.writes.append((memaddr, data))


def test_calibration_read_and_ctrl_registers_written():
    fake = FakeI2C(27504, 26435, -1000, adc_t=519888)
    BME280(fake)
    written_regs = [reg for reg, _ in fake.writes]
    assert _REG_CTRL_HUM in written_regs
    assert _REG_CTRL_MEAS in written_regs


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
