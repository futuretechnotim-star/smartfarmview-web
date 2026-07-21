"""BME280 temperature reader (temperature-only — no humidity/pressure).

Same physical part as the gateway's SparkFun Environmental Combo Breakout
(``adafruit_bme280`` on the Pi), reimplemented here without CircuitPython/Blinka
so it runs on stock MicroPython. Only temperature is decoded since that's all
the fan thermostat needs; humidity/pressure compensation can be added the same
way if a future use needs them.

Uses the standard Bosch BME280 integer compensation formula (datasheet section
4.2.3). Default I2C address 0x77 (SDO tied high, as on the SparkFun board).
"""

import ustruct  # type: ignore[import-not-found]
from machine import I2C  # type: ignore[import-not-found]

_REG_CALIB_T = 0x88  # dig_T1..dig_T3, 6 bytes
_REG_CTRL_HUM = 0xF2
_REG_CTRL_MEAS = 0xF4
_REG_TEMP = 0xFA  # temp msb/lsb/xlsb, 3 bytes

# osrs_t=1 (x1 oversampling), osrs_p=0 (skip), mode=11 (normal).
_CTRL_MEAS_NORMAL = 0b001_000_11


class BME280:
    def __init__(self, i2c: I2C, addr: int = 0x77) -> None:
        self._i2c = i2c
        self._addr = addr
        calib = self._i2c.readfrom_mem(addr, _REG_CALIB_T, 6)
        self._dig_t1 = ustruct.unpack_from("<H", calib, 0)[0]
        self._dig_t2 = ustruct.unpack_from("<h", calib, 2)[0]
        self._dig_t3 = ustruct.unpack_from("<h", calib, 4)[0]
        self._i2c.writeto_mem(addr, _REG_CTRL_HUM, bytes([0x00]))  # humidity off
        self._i2c.writeto_mem(addr, _REG_CTRL_MEAS, bytes([_CTRL_MEAS_NORMAL]))

    def temperature(self) -> float:
        """Return temperature in degrees Celsius."""
        raw = self._i2c.readfrom_mem(self._addr, _REG_TEMP, 3)
        adc_t = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)

        var1 = ((adc_t >> 3) - (self._dig_t1 << 1)) * self._dig_t2 >> 11
        var2 = (
            (((adc_t >> 4) - self._dig_t1) * ((adc_t >> 4) - self._dig_t1) >> 12)
            * self._dig_t3
        ) >> 14
        t_fine = var1 + var2
        return ((t_fine * 5 + 128) >> 8) / 100.0

    def is_present(self) -> bool:
        try:
            self.temperature()
            return True
        except Exception:
            return False
