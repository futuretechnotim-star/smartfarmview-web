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
| GP14 `PIN_SHUTDOWN_REQ` | Pi GPIO input | "please halt now" |
| GP4/5/6 `RS485_TX/RX/DE` | MAX485 DI / RO / (DE+RE tied) | read charge controller |
| GP26/ADC0 `PIN_BATTERY_ADC` | divided battery voltage | backup if RS485 fails |

On the Pi side, an HA automation / small service watches `SHUTDOWN_REQ` (or the
MQTT shutdown request) and runs a clean `halt`; a systemd/HA job publishes the
heartbeat the Pico watches.

## Thresholds (baseline — TUNE FROM DATA)

| Setting | Default | Meaning |
|---|---|---|
| `SHUTDOWN_VOLTAGE` | 12.0 V | request shutdown then cut (must be **below** gateway software CRITICAL) |
| `RECOVERY_VOLTAGE` | 13.2 V | safe to re-power |
| `GRACE_SECONDS` | 90 | max wait for the Pi to halt |
| `HEARTBEAT_TIMEOUT_S` | 300 | stale heartbeat → reboot |

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
