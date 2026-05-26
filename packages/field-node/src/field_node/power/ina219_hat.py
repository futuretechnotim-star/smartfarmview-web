"""
Driver for the Pi Zero UPS HAT (B) by WatangTech/SeenGreat.
Uses the onboard INA219 battery monitor at I2C address 0x43.
See docs/power-hat.md for hardware details and DIP switch configuration.
"""

import smbus2

from field_node.power.base import PowerMonitor, PowerReading

_I2C_BUS = 1
_I2C_ADDR = 0x43

# INA219 register addresses
_REG_CONFIG = 0x00
_REG_SHUNT_VOLTAGE = 0x01
_REG_BUS_VOLTAGE = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

# Calibration for INA219 — values match SeenGreat demo code:
# R_shunt = 0.1 ohm, max current = 3.2A, current LSB = 0.1mA
_CALIBRATION_VALUE = 4096
_CURRENT_LSB_MA = 0.1
_POWER_LSB_MW = _CURRENT_LSB_MA * 20


def _read_signed_word(bus: smbus2.SMBus, addr: int, reg: int) -> int:
    raw = bus.read_word_data(addr, reg)
    swapped = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
    return swapped if swapped < 0x8000 else swapped - 0x10000


class INA219HatMonitor(PowerMonitor):
    def __init__(self) -> None:
        self._bus = smbus2.SMBus(_I2C_BUS)
        # 32V range, PGA /8, 12-bit, continuous shunt+bus
        self._bus.write_word_data(
            _I2C_ADDR,
            _REG_CONFIG,
            ((0x3F << 8) | 0xFF),
        )
        self._bus.write_word_data(
            _I2C_ADDR,
            _REG_CALIBRATION,
            ((_CALIBRATION_VALUE >> 8) | ((_CALIBRATION_VALUE & 0xFF) << 8)),
        )

    def read(self) -> PowerReading:
        raw_bus = _read_signed_word(self._bus, _I2C_ADDR, _REG_BUS_VOLTAGE)
        raw_current = _read_signed_word(self._bus, _I2C_ADDR, _REG_CURRENT)
        raw_power = _read_signed_word(self._bus, _I2C_ADDR, _REG_POWER)

        voltage_v = ((raw_bus >> 3) * 4) / 1000.0
        current_ma = raw_current * _CURRENT_LSB_MA
        power_mw = raw_power * _POWER_LSB_MW

        return PowerReading(
            voltage_v=round(voltage_v, 3),
            current_ma=round(current_ma, 2),
            power_mw=round(power_mw, 2),
        )

    def close(self) -> None:
        self._bus.close()
