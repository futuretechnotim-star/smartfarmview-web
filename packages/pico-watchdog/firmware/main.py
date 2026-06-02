"""Pico 2 W watchdog entrypoint (MicroPython).

Reads battery voltage (RS485 Modbus, with ADC fallback), runs the autonomous
power-gate state machine from ``gate_logic``, and executes the resulting
hardware action. Telemetry + the Pi heartbeat go over MQTT, but the safety loop
runs regardless of connectivity.

Flash this directory to the Pico (see ../README.md). ``secrets.py`` provides
WIFI_SSID / WIFI_PASSWORD / MQTT_HOST / MQTT_USER / MQTT_PASSWORD.
"""

import secrets  # type: ignore[import-not-found]  # git-ignored, provided at flash time
import time

import config
import gate_logic
import network  # type: ignore[import-not-found]
from machine import ADC, Pin  # type: ignore[import-not-found]
from modbus_rtu import ModbusRTU
from mqtt_link import MQTTLink
from soc import lifepo4_soc


def _connect_wifi() -> None:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
        for _ in range(20):  # ~10s max; safety loop runs regardless
            if wlan.isconnected():
                break
            time.sleep(0.5)


def _read_battery(modbus: ModbusRTU, adc: ADC) -> "tuple[float, float | None]":
    """Return ``(voltage_v, current_ma)`` from the charge controller, falling
    back to the ADC for voltage (current unavailable) if RS485 fails.

    NOTE: the ECO-WORTHY register map must be confirmed in the field (see
    docs/gateway-node.md). Until then ``_decode_voltage`` / ``_decode_current_ma``
    use placeholder scalings.
    """
    regs = modbus.read_holding(
        config.MODBUS_SLAVE_ID, config.MODBUS_READ_ADDR, config.MODBUS_READ_COUNT
    )
    if regs is not None:
        voltage = _decode_voltage(regs)
        if voltage is not None:
            return voltage, _decode_current_ma(regs)
    # Fallback: divided battery voltage on the ADC (no current reading).
    raw = adc.read_u16() / 65535.0
    return raw * config.ADC_REF_VOLTAGE * config.ADC_DIVIDER_RATIO, None


def _decode_voltage(regs: "list[int]") -> "float | None":
    # Placeholder mapping — replace with the verified ECO-WORTHY register index.
    if not regs:
        return None
    return regs[0] / 100.0


def _decode_current_ma(regs: "list[int]") -> "float | None":
    # Placeholder: signed net battery current, register scaled ×100 A → mA.
    # + = charging, - = discharging. Verify index/sign against the BW0F app.
    if len(regs) < 2:
        return None
    raw = regs[1]
    if raw >= 0x8000:  # two's-complement signed 16-bit
        raw -= 0x10000
    return raw / 100.0 * 1000.0


def main() -> None:
    power_en = Pin(config.PIN_PI_POWER_EN, Pin.OUT, value=1)  # Pi powered on at boot
    shutdown_req = Pin(config.PIN_SHUTDOWN_REQ, Pin.OUT, value=0)
    adc = ADC(Pin(config.PIN_BATTERY_ADC))
    modbus = ModbusRTU(
        1, config.PIN_RS485_TX, config.PIN_RS485_RX, config.PIN_RS485_DE, config.RS485_BAUD
    )

    _connect_wifi()
    link = MQTTLink("gateway-pico", secrets.MQTT_HOST, secrets.MQTT_USER, secrets.MQTT_PASSWORD)
    link.connect()

    state = gate_logic.PI_ON
    state_since = time.time()
    last_telemetry = 0.0

    while True:
        link.poll()
        voltage, current_ma = _read_battery(modbus, adc)
        now = time.time()
        new_state, action = gate_logic.decide(
            state,
            voltage,
            now - state_since,
            link.heartbeat_age_s(),
            shutdown_voltage=config.SHUTDOWN_VOLTAGE,
            recovery_voltage=config.RECOVERY_VOLTAGE,
            grace_seconds=config.GRACE_SECONDS,
            heartbeat_timeout_s=config.HEARTBEAT_TIMEOUT_S,
        )

        if action == gate_logic.ACTION_REQUEST_SHUTDOWN:
            shutdown_req.value(1)
        elif action == gate_logic.ACTION_CUT_POWER:
            power_en.value(0)
            shutdown_req.value(0)
        elif action == gate_logic.ACTION_RESTORE_POWER:
            shutdown_req.value(0)
            power_en.value(1)
        elif action == gate_logic.ACTION_REBOOT:
            power_en.value(0)
            time.sleep(5)
            power_en.value(1)
            link.reset_heartbeat()  # don't immediately re-trigger the watchdog

        if new_state != state:
            state = new_state
            state_since = now

        if now - last_telemetry >= config.TELEMETRY_INTERVAL_S:
            # Contract consumed by gateway-node: soc_pct (required) + current_ma.
            link.publish_telemetry(
                {
                    "soc_pct": lifepo4_soc(voltage),
                    "current_ma": current_ma,
                    "voltage_v": round(voltage, 2),
                    "gate_state": state,
                    "last_action": action,
                    "heartbeat_age_s": round(link.heartbeat_age_s(), 1),
                }
            )
            last_telemetry = now

        time.sleep(config.LOOP_INTERVAL_S)


if __name__ == "__main__":
    main()
