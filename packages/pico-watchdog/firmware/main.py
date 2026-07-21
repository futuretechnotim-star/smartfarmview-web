"""Pico 2 W watchdog entrypoint (MicroPython).

Reads battery voltage from the INA3221 over I2C (primary) or the ADC
voltage divider on GP26 (fallback), runs the autonomous power-gate state
machine from ``gate_logic``, and executes the resulting hardware action.
Telemetry goes over MQTT but the safety loop runs regardless of
connectivity.

Flash this directory to the Pico (see ../README.md). ``secrets.py``
provides WIFI_SSID / WIFI_PASSWORD / MQTT_HOST / MQTT_USER / MQTT_PASSWORD.

Wiring (INA3221 + BME280, both on shared I2C0 bus):
  VCC  → Pico 3.3V
  GND  → Pico GND  (= battery negative)
  SDA  → GP4
  SCL  → GP5
  IN1- → battery positive (up to 26 V; INA3221 measures voltage here)
  IN1+ → battery positive (tie to IN1- — no shunt current needed)
  BME280 addr 0x77 shares the same SDA/SCL; no extra pins needed.

Wiring (fan relay):
  Relay IN → GP17 (PIN_FAN_RELAY)

Wiring (gateway PSU relay):
  Relay IN → GP16 (PIN_PSU_RELAY) — mirrors PIN_PI_POWER_EN in lockstep, as a
  second, more robust cutoff on the PSU itself.

Wiring (halt confirmation from the Pi's gpio-poweroff overlay):
  Pi GPIO27 → GP18 (PIN_HALT_CONFIRMED). Active HIGH, only driven once the
  Pi's OS halt has actually completed (see docs/gateway-node.md). Lets the
  gate cut power immediately instead of waiting out the grace-period guess.
"""

import secrets  # type: ignore[import-not-found]  # git-ignored, provided at flash time
import time

import config
import fan_logic
import gate_logic
import network  # type: ignore[import-not-found]
import ota
from bme280 import BME280
from ina3221 import INA3221
from machine import ADC, I2C, Pin  # type: ignore[import-not-found]
from mqtt_link import MQTTLink
from soc import lifepo4_soc


def _connect_wifi() -> None:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
        for _ in range(20):  # ~10 s max; safety loop continues regardless
            if wlan.isconnected():
                break
            time.sleep(0.5)


def _read_voltage(ina: "INA3221 | None", adc: ADC) -> tuple[float, str]:
    """Return ``(voltage_v, source)`` where source is 'i2c' or 'adc'."""
    if ina is not None:
        try:
            v = ina.bus_voltage(config.INA3221_CHANNEL)
            if v > 0.5:  # reject zero/garbage and fall through to ADC
                return v, "i2c"
        except Exception:
            pass
    raw = adc.read_u16() / 65535.0
    return raw * config.ADC_REF_VOLTAGE * config.ADC_DIVIDER_RATIO, "adc"


def _read_temperature(bme: "BME280 | None") -> "float | None":
    if bme is None:
        return None
    try:
        return bme.temperature()
    except Exception:
        return None


def main() -> None:
    power_en = Pin(config.PIN_PI_POWER_EN, Pin.OUT, value=1)
    psu_relay = Pin(config.PIN_PSU_RELAY, Pin.OUT, value=1)  # mirrors power_en
    shutdown_req = Pin(config.PIN_SHUTDOWN_REQ, Pin.OUT, value=0)
    fan_relay = Pin(config.PIN_FAN_RELAY, Pin.OUT, value=0)
    halt_confirmed_pin = Pin(config.PIN_HALT_CONFIRMED, Pin.IN, Pin.PULL_DOWN)
    adc = ADC(Pin(config.PIN_BATTERY_ADC))

    i2c = I2C(0, sda=Pin(config.PIN_I2C_SDA), scl=Pin(config.PIN_I2C_SCL),
              freq=config.I2C_FREQ)
    try:
        ina: INA3221 | None = INA3221(i2c, addr=config.INA3221_ADDR)
    except Exception:
        ina = None  # board not connected yet; ADC fallback will be used
    try:
        bme: BME280 | None = BME280(i2c, addr=config.BME280_ADDR)
    except Exception:
        bme = None  # sensor not connected yet; fan stays in its last state

    _connect_wifi()
    link = MQTTLink(
        "gateway-pico",
        secrets.MQTT_HOST,
        secrets.MQTT_USER,
        secrets.MQTT_PASSWORD,
    )
    link.connect()
    last_mqtt_reconnect_attempt = time.time()

    state = gate_logic.PI_ON
    state_since = time.time()
    last_telemetry = 0.0
    fan_on = fan_logic.FAN_OFF

    while True:
        now = time.time()
        if not link.is_connected() and now - last_mqtt_reconnect_attempt >= config.MQTT_RECONNECT_INTERVAL_S:
            link.connect()
            last_mqtt_reconnect_attempt = now

        link.poll()

        ota_base_url = link.pop_pending_ota()
        if ota_base_url is not None:
            if state == gate_logic.PI_ON:
                link.publish_telemetry({"gate_state": state, "ota_status": "applying"})
                error = ota.apply_update(ota_base_url)  # resets the board on success
                if error is not None:
                    link.publish_telemetry({"gate_state": state, "ota_status": "failed", "ota_error": error})
            else:
                link.publish_telemetry({"gate_state": state, "ota_status": "skipped_not_pi_on"})

        voltage, v_source = _read_voltage(ina, adc)
        enclosure_temp_c = _read_temperature(bme)

        if enclosure_temp_c is not None:
            fan_on = fan_logic.decide(
                fan_on,
                enclosure_temp_c,
                on_temp_c=config.FAN_ON_TEMP_C,
                off_temp_c=config.FAN_OFF_TEMP_C,
            )
            fan_relay.value(1 if fan_on else 0)

        new_state, action = gate_logic.decide(
            state,
            voltage,
            now - state_since,
            link.heartbeat_age_s(),
            shutdown_voltage=config.SHUTDOWN_VOLTAGE,
            recovery_voltage=config.RECOVERY_VOLTAGE,
            grace_seconds=config.GRACE_SECONDS,
            heartbeat_timeout_s=config.HEARTBEAT_TIMEOUT_S,
            halt_confirmed=bool(halt_confirmed_pin.value()),
        )

        if action == gate_logic.ACTION_REQUEST_SHUTDOWN:
            shutdown_req.value(1)
        elif action == gate_logic.ACTION_CUT_POWER:
            power_en.value(0)
            psu_relay.value(0)
            shutdown_req.value(0)
        elif action == gate_logic.ACTION_RESTORE_POWER:
            shutdown_req.value(0)
            power_en.value(1)
            psu_relay.value(1)
        elif action == gate_logic.ACTION_REBOOT:
            power_en.value(0)
            psu_relay.value(0)
            time.sleep(5)
            power_en.value(1)
            psu_relay.value(1)
            link.reset_heartbeat()

        if new_state != state:
            state = new_state
            state_since = now

        if now - last_telemetry >= config.TELEMETRY_INTERVAL_S:
            link.publish_telemetry(
                {
                    "soc_pct": lifepo4_soc(voltage),
                    "voltage_v": round(voltage, 2),
                    "v_source": v_source,
                    "gate_state": state,
                    "last_action": action,
                    "halt_confirmed": bool(halt_confirmed_pin.value()),
                    "heartbeat_age_s": round(link.heartbeat_age_s(), 1),
                    "enclosure_temp_c": (
                        round(enclosure_temp_c, 1) if enclosure_temp_c is not None else None
                    ),
                    "fan_on": fan_on,
                }
            )
            last_telemetry = now

        time.sleep(config.LOOP_INTERVAL_S)


if __name__ == "__main__":
    main()
