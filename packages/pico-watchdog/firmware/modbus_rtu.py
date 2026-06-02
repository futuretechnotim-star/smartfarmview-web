"""Minimal Modbus RTU master for the Pico (MicroPython).

Just enough to poll an ECO-WORTHY PWM solar charge controller over RS485 via a
MAX485 transceiver: function 0x03 (read holding registers) with software DE/RE
direction control and CRC-16/Modbus. Decoding of specific registers (battery
voltage, charge current, panel V/I) is deferred to the caller once the register
map is confirmed against a multimeter / the BW0F app — see docs/gateway-node.md.
"""

import time

from machine import UART, Pin  # type: ignore[import-not-found]  # MicroPython builtin


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


class ModbusRTU:
    def __init__(self, uart_id: int, tx: int, rx: int, de: int, baud: int) -> None:
        self._uart = UART(uart_id, baudrate=baud, tx=Pin(tx), rx=Pin(rx), timeout=200)
        self._de = Pin(de, Pin.OUT)
        self._de.value(0)  # receive by default

    def read_holding(self, slave: int, addr: int, count: int) -> "list[int] | None":
        """Read ``count`` holding registers; return a list of 16-bit ints, or
        ``None`` on timeout / CRC error (caller falls back to the ADC)."""
        req = bytes([slave, 0x03, addr >> 8, addr & 0xFF, count >> 8, count & 0xFF])
        crc = _crc16(req)
        frame = req + bytes([crc & 0xFF, crc >> 8])

        self._de.value(1)  # transmit
        self._uart.write(frame)
        # Wait for the bytes to clock out before flipping back to receive.
        time.sleep_ms(10)  # type: ignore[attr-defined]  # MicroPython time
        self._de.value(0)  # receive

        expected = 5 + 2 * count  # addr+func+bytecount + data + crc
        deadline = time.ticks_add(time.ticks_ms(), 300)  # type: ignore[attr-defined]
        buf = b""
        while len(buf) < expected and time.ticks_diff(deadline, time.ticks_ms()) > 0:  # type: ignore[attr-defined]
            chunk = self._uart.read(expected - len(buf))
            if chunk:
                buf += chunk

        if len(buf) < expected or buf[1] != 0x03:
            return None
        if _crc16(buf[:-2]) != (buf[-2] | (buf[-1] << 8)):
            return None

        data = buf[3:-2]
        return [(data[i] << 8) | data[i + 1] for i in range(0, len(data), 2)]
