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
  Relay IN → GP16 (PIN_PSU_RELAY) — a second, more robust cutoff on the PSU
  itself. Wired through the relay's NC contact (not NO): COM ← solar charge
  controller load(+), NC → PSU(+). De-energized (the pin's state during the
  Pico's own boot/reset gap) closes NC and lets power flow — see
  _PSU_RELAY_ON/_PSU_RELAY_OFF below. A Pico reboot no longer glitches this
  relay's output; only a deliberate cut does.

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
import ota_prep
from bme280 import BME280
from ina3221 import INA3221
from local_log import RollingLog
from machine import ADC, I2C, Pin, reset  # type: ignore[import-not-found]
from mqtt_link import MQTTLink
from soc import lifepo4_soc


def _connect_wifi(wlan: "network.WLAN") -> None:
    """Attempt one connection. Callers must retry periodically — this alone
    only tries for ~10s, and if the AP isn't up yet at that exact moment
    (e.g. the gateway restarting around the same time as the Pico), a
    one-shot attempt at boot leaves the Pico permanently disconnected for the
    rest of that power cycle."""
    # Full radio reset before connecting. MicroPython WiFi can wedge after the
    # AP drops — which it does on every gateway low-voltage cut / OTA / reboot —
    # and a bare active(True)+connect() won't recover (isconnected() can even
    # sit stale-True on a dead link). Bouncing active() re-inits the radio so
    # re-association actually works. This is what lets a far, marginal node
    # (or this one) self-heal unattended in the field instead of needing a
    # physical power-cycle.
    try:  # noqa: SIM105
        wlan.disconnect()
    except Exception:  # noqa: BLE001 — best-effort; may not be connected
        pass
    wlan.active(False)
    time.sleep(0.5)
    wlan.active(True)
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


def _read_ina_channel(ina: "INA3221 | None", channel: int) -> "float | None":
    """Best-effort read of a non-safety-critical INA3221 channel (load/solar)
    — unlike _read_voltage, no ADC fallback and no impact on gate_logic;
    just diagnostic visibility in telemetry."""
    if ina is None:
        return None
    try:
        return ina.bus_voltage(channel)
    except Exception:
        return None


def _read_temperature(bme: "BME280 | None") -> "float | None":
    if bme is None:
        return None
    try:
        return bme.temperature()
    except Exception:
        return None


def _read_humidity(bme: "BME280 | None") -> "float | None":
    if bme is None:
        return None
    try:
        return bme.humidity()
    except Exception:
        return None


def _prepare_and_apply_ota(link: MQTTLink, halt_confirmed_pin: Pin, base_url: str) -> None:
    """Stage the update (needs the gateway's own network/AP, so this runs
    first, before anything is asked to halt), then ask the gateway to
    gracefully halt and wait for confirmation before activating.

    Activating ends in machine.reset(), which briefly leaves the relay pins
    undriven — until the relay's own wiring makes that fail-safe (see
    docs/gateway-node.md), that reboot must not land on a live Pi. Activation
    itself needs no network, which is exactly why staging happens first: by
    the time the gateway has actually halted, its AP is down too, so the file
    server a single-step apply would still be trying to reach is already
    unreachable.
    """
    link.publish_telemetry({"ota_status": "staging"})
    stage_error = ota.stage_update(base_url)
    if stage_error is not None:
        link.publish_telemetry({"ota_status": "stage_failed", "ota_error": stage_error})
        return

    if not link.is_connected():
        link.connect()
    link.publish(config.GATEWAY_CMD_TOPIC, {"cmd": "prepare_shutdown"})

    start = time.time()
    while True:
        # Retry the connection here too (unlike the outer loop's slow 300s
        # backoff) — a dropped link during this window would otherwise make
        # every publish() below silently no-op, running the whole wait to
        # completion invisibly. halt_confirmed itself is a direct GPIO read
        # and doesn't depend on this — the gateway's AP going down mid-wait
        # (expected, as part of its own halt) just means these status
        # publishes stop landing anywhere, not that the wait itself breaks.
        if not link.is_connected():
            link.connect()
        link.poll()
        halt_confirmed = bool(halt_confirmed_pin.value())
        elapsed = time.time() - start
        outcome = ota_prep.decide_ota_wait(halt_confirmed, elapsed, config.OTA_SHUTDOWN_TIMEOUT_S)

        if outcome == ota_prep.PROCEED:
            break
        if outcome == ota_prep.ABORT:
            link.publish_telemetry({"ota_status": "aborted_gateway_did_not_halt"})
            return

        link.publish_telemetry(
            {"ota_status": "waiting_for_gateway_halt", "elapsed_s": round(elapsed, 1)}
        )
        time.sleep(config.LOOP_INTERVAL_S)

    link.publish_telemetry({"ota_status": "activating"})
    error = ota.activate_staged()  # resets the board on success
    if error is not None:
        link.publish_telemetry({"ota_status": "activate_failed", "ota_error": error})


# PIN_PSU_RELAY specifically is wired through the relay's NC (Normally
# Closed) contact, not NO — confirmed on hardware: with the signal line
# disconnected (the same undefined state the pin sits in during the Pico's
# own boot/reset gap), the relay de-energizes and NC carries battery voltage
# through to the load while NO reads 0V. That makes de-energized == powered
# the fail-safe default: a Pico reboot no longer glitches the PSU relay's
# output, only a deliberate, actively-driven cut does. PIN_PI_POWER_EN is a
# direct buck-enable line (not a relay) and is unaffected — plain active-high.
_PSU_RELAY_ON = 0  # de-energized -> NC closed -> power flows
_PSU_RELAY_OFF = 1  # energized -> NC open -> power cut


def main() -> None:
    log = RollingLog(config.LOCAL_LOG_PATH, max_bytes=config.LOCAL_LOG_MAX_BYTES)
    log.append("boot")

    power_en = Pin(config.PIN_PI_POWER_EN, Pin.OUT, value=1)
    psu_relay = Pin(config.PIN_PSU_RELAY, Pin.OUT, value=_PSU_RELAY_ON)  # NC-wired — see above
    shutdown_req = Pin(config.PIN_SHUTDOWN_REQ, Pin.OUT, value=0)
    fan_relay = Pin(config.PIN_FAN_RELAY, Pin.OUT, value=0)
    halt_confirmed_pin = Pin(config.PIN_HALT_CONFIRMED, Pin.IN, Pin.PULL_DOWN)
    adc = ADC(Pin(config.PIN_BATTERY_ADC))

    i2c = I2C(0, sda=Pin(config.PIN_I2C_SDA), scl=Pin(config.PIN_I2C_SCL), freq=config.I2C_FREQ)
    try:
        ina: INA3221 | None = INA3221(i2c, addr=config.INA3221_ADDR)
    except Exception:
        ina = None  # board not connected yet; ADC fallback will be used
    try:
        bme: BME280 | None = BME280(i2c, addr=config.BME280_ADDR)
    except Exception:
        bme = None  # sensor not connected yet; fan stays in its last state

    wlan = network.WLAN(network.STA_IF)
    _connect_wifi(wlan)
    log.append(f"wifi_connect_attempt result={wlan.isconnected()}")
    last_wifi_reconnect_attempt = time.time()
    link = MQTTLink(
        "gateway-pico",
        secrets.MQTT_HOST,
        secrets.MQTT_USER,
        secrets.MQTT_PASSWORD,
    )
    link.connect()
    log.append(f"mqtt_connect_attempt result={link.is_connected()}")
    last_mqtt_reconnect_attempt = time.time()

    state = gate_logic.PI_ON
    state_since = time.time()
    last_telemetry = 0.0
    fan_on = fan_logic.FAN_OFF
    mqtt_was_connected = False
    last_mqtt_ok = time.time()

    while True:
        now = time.time()
        if (
            not wlan.isconnected()
            and now - last_wifi_reconnect_attempt >= config.WIFI_RECONNECT_INTERVAL_S
        ):
            _connect_wifi(wlan)
            log.append(f"wifi_reconnect_attempt result={wlan.isconnected()}")
            last_wifi_reconnect_attempt = now

        if (
            not link.is_connected()
            and now - last_mqtt_reconnect_attempt >= config.MQTT_RECONNECT_INTERVAL_S
        ):
            link.connect()
            log.append(f"mqtt_reconnect_attempt result={link.is_connected()}")
            last_mqtt_reconnect_attempt = now

        link.poll()

        # Forward the log tail whenever MQTT (re)connects, so events logged
        # while the broker was down (cuts/reboots) reach HA without a USB cable.
        if link.is_connected() and not mqtt_was_connected:
            link.publish(config.LOG_TOPIC, {"log": log.tail(config.LOG_TAIL_LINES)}, retain=True)
        mqtt_was_connected = link.is_connected()

        # Connectivity self-heal (last resort). While the Pi should be up
        # (PI_ON) the broker is reachable, so sustained MQTT loss means our own
        # link is wedged in a way even the radio-bounce reconnect hasn't
        # cleared — reboot to recover unattended. Safe while PI_ON: the NC relay
        # keeps the Pi powered across the Pico's reset. Gated on PI_ON so a
        # legitimate broker-down window (Pi off in PI_OFF) never triggers it.
        if state == gate_logic.PI_ON and link.is_connected():
            last_mqtt_ok = now
        elif state == gate_logic.PI_ON and now - last_mqtt_ok >= config.NET_RECOVERY_TIMEOUT_S:
            log.append(f"net_recovery_reset offline_s={round(now - last_mqtt_ok)}")
            time.sleep(0.3)  # let the flash write land before we reset
            reset()

        ota_base_url = link.pop_pending_ota()
        if ota_base_url is not None:
            if state == gate_logic.PI_ON:
                _prepare_and_apply_ota(link, halt_confirmed_pin, ota_base_url)
            else:
                link.publish_telemetry({"gate_state": state, "ota_status": "skipped_not_pi_on"})

        voltage, v_source = _read_voltage(ina, adc)
        load_voltage_v = _read_ina_channel(ina, config.INA3221_CH_LOAD_5V)
        solar_voltage_v = _read_ina_channel(ina, config.INA3221_CH_SOLAR)
        enclosure_temp_c = _read_temperature(bme)
        enclosure_humidity_pct = _read_humidity(bme)

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
            hard_cutoff_voltage=config.HARD_CUTOFF_VOLTAGE,
            recovery_voltage=config.RECOVERY_VOLTAGE,
            grace_seconds=config.GRACE_SECONDS,
            heartbeat_timeout_s=config.HEARTBEAT_TIMEOUT_S,
            halt_confirmed=bool(halt_confirmed_pin.value()),
            halt_settle_seconds=config.HALT_SETTLE_SECONDS,
            mqtt_connected=link.is_connected(),
        )

        if action != gate_logic.ACTION_NONE:
            log.append(
                f"action={action} state={state} new_state={new_state} voltage={voltage:.2f} "
                f"v_source={v_source} heartbeat_age_s={link.heartbeat_age_s():.1f} "
                f"halt_confirmed={halt_confirmed_pin.value()}"
            )

        if action == gate_logic.ACTION_REQUEST_SHUTDOWN:
            shutdown_req.value(1)
        elif action == gate_logic.ACTION_CUT_POWER:
            power_en.value(0)
            psu_relay.value(_PSU_RELAY_OFF)
            shutdown_req.value(0)
        elif action == gate_logic.ACTION_RESTORE_POWER:
            shutdown_req.value(0)
            power_en.value(1)
            psu_relay.value(_PSU_RELAY_ON)
        elif action == gate_logic.ACTION_REBOOT:
            power_en.value(0)
            psu_relay.value(_PSU_RELAY_OFF)
            time.sleep(5)
            power_en.value(1)
            psu_relay.value(_PSU_RELAY_ON)
            link.reset_heartbeat()

        if new_state != state:
            state = new_state
            state_since = now

        if now - last_telemetry >= config.TELEMETRY_INTERVAL_S:
            log.append(
                f"snapshot state={state} voltage={voltage:.2f} v_source={v_source} "
                f"soc_pct={lifepo4_soc(voltage)} heartbeat_age_s={link.heartbeat_age_s():.1f} "
                f"halt_confirmed={halt_confirmed_pin.value()} wifi={wlan.isconnected()} "
                f"mqtt={link.is_connected()} fan_on={fan_on}"
            )
            link.publish_telemetry(
                {
                    "soc_pct": lifepo4_soc(voltage),
                    "voltage_v": round(voltage, 2),
                    "v_source": v_source,
                    "load_voltage_v": (
                        round(load_voltage_v, 2) if load_voltage_v is not None else None
                    ),
                    "solar_voltage_v": (
                        round(solar_voltage_v, 2) if solar_voltage_v is not None else None
                    ),
                    "gate_state": state,
                    "last_action": action,
                    "halt_confirmed": bool(halt_confirmed_pin.value()),
                    "heartbeat_age_s": round(link.heartbeat_age_s(), 1),
                    "enclosure_temp_c": (
                        round(enclosure_temp_c, 1) if enclosure_temp_c is not None else None
                    ),
                    "enclosure_humidity_pct": (
                        round(enclosure_humidity_pct, 1)
                        if enclosure_humidity_pct is not None
                        else None
                    ),
                    "fan_on": fan_on,
                }
            )
            last_telemetry = now

        time.sleep(config.LOOP_INTERVAL_S)


if __name__ == "__main__":
    main()
