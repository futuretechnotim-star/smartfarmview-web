"""BME280 temperature + humidity reader (no pressure — unused by this project).

Same physical part as the gateway's SparkFun Environmental Combo Breakout
(``adafruit_bme280`` on the Pi), reimplemented here without CircuitPython/Blinka
so it runs on stock MicroPython. Pressure is left uncompensated since nothing
here needs it; humidity/pressure compensation can be extended the same way if a
future use needs pressure too.

Uses the standard Bosch BME280 compensation formulas (datasheet section 4.2.3):
integer formula for temperature, floating-point formula for humidity (simpler
and less error-prone than the integer one's bit-shift sign handling, and the
precision loss on a single-precision MicroPython float is negligible for a
one-decimal-place reading). Default I2C address 0x77 (SDO tied high, as on the
SparkFun board).
"""

import ustruct  # type: ignore[import-not-found]
from machine import I2C  # type: ignore[import-not-found]

_REG_CALIB_T = 0x88  # dig_T1..dig_T3, 6 bytes
_REG_CALIB_H1 = 0xA1  # dig_H1, 1 byte
_REG_CALIB_H2_6 = 0xE1  # dig_H2, dig_H3, then raw bytes for dig_H4/H5, dig_H6 — 7 bytes
_REG_CTRL_HUM = 0xF2
_REG_CTRL_MEAS = 0xF4
_REG_TEMP = 0xFA  # temp msb/lsb/xlsb, 3 bytes
_REG_HUM = 0xFD  # hum msb/lsb, 2 bytes

# osrs_h=1 (x1 oversampling). Per datasheet, a ctrl_hum write only takes effect
# once ctrl_meas is subsequently written, so __init__ writes this first.
_CTRL_HUM_OSRS_1 = 0b001
# osrs_t=1 (x1 oversampling), osrs_p=0 (skip), mode=11 (normal).
_CTRL_MEAS_NORMAL = 0b001_000_11


class BME280:
    def __init__(self, i2c: I2C, addr: int = 0x77) -> None:
        self._i2c = i2c
        self._addr = addr
        self._t_fine = 0

        calib = self._i2c.readfrom_mem(addr, _REG_CALIB_T, 6)
        self._dig_t1 = ustruct.unpack_from("<H", calib, 0)[0]
        self._dig_t2 = ustruct.unpack_from("<h", calib, 2)[0]
        self._dig_t3 = ustruct.unpack_from("<h", calib, 4)[0]

        self._dig_h1 = self._i2c.readfrom_mem(addr, _REG_CALIB_H1, 1)[0]
        calib_h = self._i2c.readfrom_mem(addr, _REG_CALIB_H2_6, 7)
        self._dig_h2 = ustruct.unpack_from("<h", calib_h, 0)[0]
        self._dig_h3 = calib_h[2]
        e4, e5, e6 = calib_h[3], calib_h[4], calib_h[5]
        dig_h4 = (e4 << 4) | (e5 & 0x0F)
        if dig_h4 & 0x800:
            dig_h4 -= 0x1000
        self._dig_h4 = dig_h4
        dig_h5 = (e6 << 4) | (e5 >> 4)
        if dig_h5 & 0x800:
            dig_h5 -= 0x1000
        self._dig_h5 = dig_h5
        self._dig_h6 = ustruct.unpack_from("<b", calib_h, 6)[0]

        self._i2c.writeto_mem(addr, _REG_CTRL_HUM, bytes([_CTRL_HUM_OSRS_1]))
        self._i2c.writeto_mem(addr, _REG_CTRL_MEAS, bytes([_CTRL_MEAS_NORMAL]))

    def temperature(self) -> float:
        """Return temperature in degrees Celsius. Also refreshes the t_fine
        value humidity() needs — call this before humidity() for a consistent
        reading pair."""
        raw = self._i2c.readfrom_mem(self._addr, _REG_TEMP, 3)
        adc_t = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)

        var1 = ((adc_t >> 3) - (self._dig_t1 << 1)) * self._dig_t2 >> 11
        var2 = (
            (((adc_t >> 4) - self._dig_t1) * ((adc_t >> 4) - self._dig_t1) >> 12) * self._dig_t3
        ) >> 14
        self._t_fine = var1 + var2
        return ((self._t_fine * 5 + 128) >> 8) / 100.0

    def humidity(self) -> float:
        """Return relative humidity in percent (0-100). Reads temperature
        first to get a fresh t_fine — humidity compensation depends on it."""
        self.temperature()
        raw = self._i2c.readfrom_mem(self._addr, _REG_HUM, 2)
        adc_h = (raw[0] << 8) | raw[1]

        var_h = self._t_fine - 76800.0
        var_h = (adc_h - (self._dig_h4 * 64.0 + self._dig_h5 / 16384.0 * var_h)) * (
            self._dig_h2
            / 65536.0
            * (1.0 + self._dig_h6 / 67108864.0 * var_h * (1.0 + self._dig_h3 / 67108864.0 * var_h))
        )
        var_h = var_h * (1.0 - self._dig_h1 * var_h / 524288.0)
        if var_h > 100.0:
            var_h = 100.0
        elif var_h < 0.0:
            var_h = 0.0
        return var_h

    def is_present(self) -> bool:
        try:
            self.temperature()
            return True
        except Exception:
            return False
