"""INA3221 triple-channel bus-voltage reader (voltage-only).

Reads the bus voltage on up to three independent channels over I2C.
The bus voltage is measured at each IN_N- pin relative to GND — connect
IN_N- to the rail you want to measure; the onboard shunt carries no
current so shunt readings are ignored.

Resolution: 8 mV/LSB.  Range: 0–26 V per channel.
Default I2C address 0x40 (ADDR pin tied to GND on most breakout boards).

No external dependencies — compatible with MicroPython and CPython (for
unit testing with a mock I2C object).
"""

from machine import I2C  # type: ignore[import-not-found]

_REG_CONFIG = 0x00
_REG_BUS_V = {1: 0x02, 2: 0x04, 3: 0x06}
_REG_MANUF_ID = 0xFF

# All three channels enabled, 1 sample average, 1.1 ms conversion, continuous.
_CONFIG_CONTINUOUS = 0x7127


class INA3221:
    def __init__(self, i2c: I2C, addr: int = 0x40) -> None:
        self._i2c = i2c
        self._addr = addr
        self._write(_REG_CONFIG, _CONFIG_CONTINUOUS)

    def _write(self, reg: int, value: int) -> None:
        self._i2c.writeto(self._addr, bytes([reg, value >> 8, value & 0xFF]))

    def _read(self, reg: int) -> int:
        self._i2c.writeto(self._addr, bytes([reg]))
        d = self._i2c.readfrom(self._addr, 2)
        return (d[0] << 8) | d[1]

    def bus_voltage(self, channel: int) -> float:
        """Return bus voltage in volts for channel 1, 2, or 3.

        Bits [15:3] of the register hold the 13-bit unsigned voltage value;
        bits [2:0] are flags.  Each LSB = 8 mV.
        """
        if channel not in _REG_BUS_V:
            raise ValueError("channel must be 1, 2, or 3")
        raw = self._read(_REG_BUS_V[channel])
        return (raw >> 3) * 0.008

    def is_present(self) -> bool:
        """Return True if an INA3221 responds at the configured address."""
        try:
            manuf = self._read(_REG_MANUF_ID)
            return manuf == 0x5449  # Texas Instruments ASCII "TI"
        except Exception:
            return False
