# Pico 2 W Watchdog — Power Controller

The Raspberry Pi **Pico 2 W** is the gateway's independent power controller: the
*enforcing* tier of the [two-tier power design](gateway-node.md). Firmware lives
in [`packages/pico-watchdog/`](../packages/pico-watchdog/).

## Why a separate device is required

A Pi 5 **cannot wake itself after a deep-battery shutdown** — its RTC wake needs
the 5V rail kept alive, and a timed wake can't know if the battery recovered. The
Pico is always powered from the battery (sub-0.3W via its own regulator), so it
can keep the Pi off through a recharge and re-power it when voltage recovers.

## Responsibilities (autonomous — no network required)

1. **Battery/solar telemetry** — poll the ECO-WORTHY PWM controller over RS485
   Modbus (ADC fallback for voltage if RS485 fails).
2. **Low-voltage graceful shutdown** — assert `SHUTDOWN_REQ`, wait the grace
   period for the Pi to halt, then cut the 5V buck enable.
3. **Wake-on-recharge** — keep the Pi off until voltage clears the (higher)
   recovery threshold, then restore power. Hysteresis prevents dawn boot-loops.
4. **Hardware watchdog** — power-cycle the Pi if its MQTT heartbeat goes stale.
5. **Telemetry + OTA** — publish state to MQTT; accept OTA updates.

The state machine is [`gate_logic.py`](../packages/pico-watchdog/firmware/gate_logic.py)
— pure and unit-tested under CPython because it is safety-critical:

```
PI_ON ──(V ≤ shutdown_voltage)──► SHUTTING_DOWN ──(grace elapsed)──► PI_OFF
  ▲                                                                    │
  └──────────────────(V ≥ recovery_voltage)───────────────────────────┘
PI_ON ──(heartbeat stale)──► power-cycle, stay PI_ON
```

## Pin map (see `firmware/config.py`)

| Pico pin | To | Purpose |
|---|---|---|
| GP15 `PIN_PI_POWER_EN` | 5V buck ENABLE / high-side load switch | gate the Pi's power |
| GP16 `PIN_PSU_RELAY` | gateway PSU relay IN | second, more robust cutoff, wired through the relay's **NC** contact (COM ← solar charge controller load(+), NC → PSU(+)) so a Pico reboot fails safe to *powered*, not cut |
| GP14 `PIN_SHUTDOWN_REQ` | Pi GPIO input | "please halt now" |
| GP4/GP5 `PIN_I2C_SDA/SCL` | I2C0 bus: INA3221 (`0x40`) + BME280 (`0x77`) | battery voltage + enclosure temp |
| GP26/ADC0 `PIN_BATTERY_ADC` | divided battery voltage | backup if I2C fails |
| GP17 `PIN_FAN_RELAY` | enclosure fan relay IN | thermostat-controlled fan |

Note: GP4/GP5 were originally slated for RS485 (Modbus) to the charge controller;
the shipped firmware reads voltage over I2C instead (`ina3221.py`), so those pins
are free for the BME280 to share.

On the Pi side, an HA automation / small service watches `SHUTDOWN_REQ` (or the
MQTT shutdown request) and runs a clean `halt`; a systemd/HA job publishes the
heartbeat the Pico watches.

## Enclosure fan thermostat

The Pico also reads the enclosure's BME280 temperature (same part as the
gateway's SparkFun Environmental Combo Breakout, reimplemented for MicroPython
in [`bme280.py`](../packages/pico-watchdog/firmware/bme280.py) — temperature
only, no CircuitPython/Blinka dependency) and drives a fan relay from it.

[`fan_logic.py`](../packages/pico-watchdog/firmware/fan_logic.py) is a second
pure, unit-tested state machine (same shape as `gate_logic.py`): simple
hysteresis, so the relay doesn't chatter around a single threshold.

```
FAN_OFF ──(temp ≥ FAN_ON_TEMP_C)───► FAN_ON
   ▲                                    │
   └──────(temp ≤ FAN_OFF_TEMP_C)───────┘
```

If the BME280 isn't detected (or a read fails), the fan simply holds its last
state rather than guessing — this is comfort/cooling, not safety-critical, so
it fails static rather than forcing an assumption.

## Thresholds (baseline — TUNE FROM DATA)

| Setting | Default | Meaning |
|---|---|---|
| `SHUTDOWN_VOLTAGE` | 12.0 V | request shutdown then cut (must be **below** gateway software CRITICAL) |
| `RECOVERY_VOLTAGE` | 13.2 V | safe to re-power |
| `GRACE_SECONDS` | 90 | max wait for the Pi to halt |
| `HEARTBEAT_TIMEOUT_S` | 300 | stale heartbeat → reboot |
| `FAN_ON_TEMP_C` | 35.0 | enclosure temp at/above which the fan turns on |
| `FAN_OFF_TEMP_C` | 30.0 | enclosure temp at/below which the fan turns back off |

LiFePO4's flat curve makes voltage-only SoC approximate — fine for the software
tier; the gate's hard thresholds work on raw voltage. Re-tune both the thresholds
and the [`soc.py`](../packages/pico-watchdog/firmware/soc.py) curve after the
field soak.

## Connectivity caveat (Tailscale)

There is **no native Tailscale client for the RP2350/Pico**. Therefore:

- The **safety loop never touches the network** — it runs from local voltage
  readings alone.
- **Telemetry/management** go over MQTT to the gateway broker on the mesh/AP
  network; remote operators reach the Pico **through the gateway Pi** (on
  Tailscale, acting as subnet router / proxy). [OTA](../packages/pico-watchdog/firmware/ota.py)
  is brokered the same way and only runs while the Pi is up (it serves the files).

## Flashing & test

See the [package README](../packages/pico-watchdog/README.md) for flashing
(`mpremote`, `umqtt.simple`, `urequests`) and running the pure-module tests.
