# smartfarmview-pico-watchdog

MicroPython firmware for the **Raspberry Pi Pico 2 W** acting as the gateway's
independent power controller / hardware watchdog. It is the *enforcing* tier of
the gateway's two-tier power design — see [`docs/gateway-node.md`](../../docs/gateway-node.md)
and [`docs/pico-watchdog.md`](../../docs/pico-watchdog.md).

## What it does (autonomously, no network required)

- Reads battery voltage from the INA3221 over I2C0 (ADC fallback if I2C fails).
- Runs the [`gate_logic`](firmware/gate_logic.py) state machine:
  `PI_ON → SHUTTING_DOWN → PI_OFF → PI_ON`.
  - **Low battery:** assert `SHUTDOWN_REQ`, wait the grace period, then cut the
    Pi's 5V supply (`PIN_PI_POWER_EN`) and the gateway PSU relay (`PIN_PSU_RELAY`,
    mirrored in lockstep as a second, more robust cutoff).
  - **Wake-on-recharge:** keep the Pi off until voltage recovers (hysteresis),
    then restore power. *This is the whole reason an external device is needed —
    the Pi 5 cannot wake itself after a deep-battery shutdown.*
  - **Hardware watchdog:** power-cycle the Pi if its MQTT heartbeat goes stale.
- Runs the [`fan_logic`](firmware/fan_logic.py) thermostat: reads enclosure
  temperature from a BME280 (sharing the same I2C0 bus) and drives the fan relay
  (`PIN_FAN_RELAY`) with hysteresis.
- Publishes telemetry (`soc_pct`, `voltage_v`, gate state, `enclosure_temp_c`,
  `fan_on`) to MQTT for the gateway power brain and Home Assistant.

The safety loop **never depends on WiFi/MQTT**. Connectivity is for telemetry
only, brokered through the gateway Pi (the Pico can't join Tailscale directly).
[`ota.py`](firmware/ota.py) sketches a future over-the-air update path but isn't
wired up yet — no command dispatch calls it, and there's no file server on the
gateway serving firmware files. Flashing today is physical (`mpremote`, below).

## Layout

```
firmware/        flashed to the Pico
  main.py          entrypoint + hardware glue
  gate_logic.py    pure safety state machine (tested)
  fan_logic.py     pure fan thermostat state machine (tested)
  soc.py           LiFePO4 voltage→SoC (tested)
  config.py        thresholds + pin map
  ina3221.py       battery voltage driver (tested)
  bme280.py        enclosure temperature driver (tested)
  modbus_rtu.py    minimal Modbus RTU master (unused — see note above)
  mqtt_link.py     telemetry + heartbeat
  ota.py           over-the-air update (not yet wired up — see note above)
  secrets.example.py  → copy to secrets.py (git-ignored)
tests/           CPython tests for the pure modules
```

## Wiring (see config.py for the pin map)

| Pico pin | To |
|---|---|
| `PIN_PI_POWER_EN` (GP15) | 5V buck ENABLE / high-side load switch |
| `PIN_PSU_RELAY` (GP16) | gateway PSU relay — mirrors `PIN_PI_POWER_EN` in lockstep |
| `PIN_SHUTDOWN_REQ` (GP14) | Pi GPIO "please halt" input |
| `PIN_I2C_SDA/SCL` (GP4/5) | shared I2C0: INA3221 (`0x40`) + BME280 (`0x77`) |
| `PIN_FAN_RELAY` (GP17) | enclosure fan relay — thermostat-controlled |
| `PIN_BATTERY_ADC` (GP26/ADC0) | divided battery voltage (backup) |

⚠️ Thresholds in `config.py` are starting points for the 12.8V 20Ah LiFePO4 and
**must be tuned from the field-soak dataset**. The gate's `SHUTDOWN_VOLTAGE` must
stay below the gateway software CRITICAL level so HA halts gracefully first.

## Flashing

1. Install MicroPython for the Pico 2 W (RP2350).
2. `cp firmware/secrets.example.py firmware/secrets.py` and fill it in.
3. Copy everything in `firmware/` to the Pico (e.g. `mpremote fs cp firmware/* :`)
   and install `umqtt.simple` + `urequests` (`mip install umqtt.simple urequests`).
4. Reset; `main.py` runs on boot.

## Dev (pure modules only)

```bash
cd packages/pico-watchdog
python3 -m venv .venv && source .venv/bin/activate
pip install pytest ruff mypy
ruff check firmware/ tests/ && ruff format --check firmware/ tests/
mypy firmware/gate_logic.py firmware/soc.py
pytest tests/
```
