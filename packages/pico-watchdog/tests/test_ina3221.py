"""Unit tests for INA3221 bus-voltage driver.

Uses a fake I2C object so no hardware is required.  Tests verify the
register-address selection, the bit-shift/scale arithmetic, and the
manufacturer-ID presence check.
"""

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

from ina3221 import INA3221  # noqa: E402  (import after stub)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REG_CONFIG = 0x00
_REG_BUS = {1: 0x02, 2: 0x04, 3: 0x06}
_REG_MANUF = 0xFF


class FakeI2C:
    """Records writes and serves pre-programmed register values on reads."""

    def __init__(self, registers: dict[int, int]) -> None:
        self._regs = registers
        self._last_reg: int | None = None
        self.writes: list[bytes] = []

    def writeto(self, addr: int, data: bytes) -> None:
        self.writes.append(data)
        if len(data) == 1:
            self._last_reg = data[0]

    def readfrom(self, addr: int, n: int) -> bytes:
        assert self._last_reg is not None
        value = self._regs.get(self._last_reg, 0)
        return bytes([value >> 8, value & 0xFF])


def _make_bus_raw(voltage_v: float) -> int:
    """Convert a voltage to the raw 16-bit register value the INA3221 returns."""
    lsb_count = round(voltage_v / 0.008)
    return (lsb_count & 0x1FFF) << 3  # 13-bit value in bits [15:3]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bus_voltage_ch1_exact():
    regs = {_REG_BUS[1]: _make_bus_raw(12.0)}
    ina = INA3221(FakeI2C(regs))
    assert abs(ina.bus_voltage(1) - 12.0) < 0.01


def test_bus_voltage_ch1_low():
    regs = {_REG_BUS[1]: _make_bus_raw(11.2)}
    ina = INA3221(FakeI2C(regs))
    assert abs(ina.bus_voltage(1) - 11.2) < 0.01


def test_bus_voltage_ch2():
    regs = {_REG_BUS[2]: _make_bus_raw(13.4)}
    ina = INA3221(FakeI2C(regs))
    assert abs(ina.bus_voltage(2) - 13.4) < 0.01


def test_bus_voltage_ch3():
    regs = {_REG_BUS[3]: _make_bus_raw(5.0)}
    ina = INA3221(FakeI2C(regs))
    assert abs(ina.bus_voltage(3) - 5.0) < 0.01


def test_bus_voltage_resolution():
    # One LSB = 8 mV; confirm we can distinguish adjacent steps
    v_a = 12.000
    v_b = 12.008
    ina_a = INA3221(FakeI2C({_REG_BUS[1]: _make_bus_raw(v_a)}))
    ina_b = INA3221(FakeI2C({_REG_BUS[1]: _make_bus_raw(v_b)}))
    assert ina_b.bus_voltage(1) > ina_a.bus_voltage(1)


def test_bus_voltage_zero():
    ina = INA3221(FakeI2C({_REG_BUS[1]: 0x0000}))
    assert ina.bus_voltage(1) == 0.0


def test_config_written_on_init():
    fake = FakeI2C({})
    INA3221(fake)
    # First write must target the config register (byte 0 of the payload)
    assert len(fake.writes) >= 1
    assert fake.writes[0][0] == _REG_CONFIG


def test_is_present_true():
    regs = {_REG_MANUF: 0x5449}  # "TI"
    ina = INA3221(FakeI2C(regs))
    assert ina.is_present() is True


def test_is_present_false_wrong_id():
    regs = {_REG_MANUF: 0x1234}
    ina = INA3221(FakeI2C(regs))
    assert ina.is_present() is False


def test_is_present_false_on_exception():
    class BrokenI2C(FakeI2C):
        def readfrom(self, addr: int, n: int) -> bytes:
            raise OSError("no device")

    ina = INA3221(BrokenI2C({}))
    assert ina.is_present() is False


def test_invalid_channel_raises():
    import pytest

    ina = INA3221(FakeI2C({}))
    with pytest.raises(ValueError):
        ina.bus_voltage(0)
    with pytest.raises(ValueError):
        ina.bus_voltage(4)
