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
   period (or until `HALT_CONFIRMED`) for the Pi to halt, then cut power via the
   PSU relay (GP16). GP15 `PIN_PI_POWER_EN` gates a 5V buck ENABLE on builds that
   have one; the field gateway is **relay-only**, so GP15 is unused there.
3. **Wake-on-recharge** — keep the Pi off until voltage clears the (higher)
   recovery threshold, then restore power. Hysteresis prevents dawn boot-loops.
4. **Hardware watchdog** — power-cycle the Pi if its MQTT heartbeat goes stale.
5. **Telemetry + OTA** — publish state to MQTT; accept OTA updates.

The state machine is [`gate_logic.py`](../packages/pico-watchdog/firmware/gate_logic.py)
— pure and unit-tested under CPython because it is safety-critical:

```
PI_ON ──(V ≤ shutdown_voltage)──► SHUTTING_DOWN ──(halt_confirmed | grace elapsed | V ≤ hard_cutoff)──► PI_OFF
  ▲                                                                                                      │
  └──────────────────────────────(V ≥ recovery_voltage)─────────────────────────────────────────────────┘
PI_ON ──(heartbeat stale AND mqtt connected)──► power-cycle, stay PI_ON
```

`halt_confirmed` (GP18, from the Pi's `gpio-poweroff`) lets the gate cut as soon
as the OS halt completes instead of waiting out the grace guess — but it is
honored **only while `SHUTTING_DOWN`** (a shutdown we asked for). A high GP18
while `PI_ON` is treated as spurious — a boot transient, electrical noise, or a
pin held high because a back-power path kept a halted Pi alive — and **never cuts
a running Pi**; a genuine unrequested halt is caught by the heartbeat watchdog
instead. In `PI_OFF`, GP18 is honored only for `HALT_SETTLE_SECONDS` (30 s) after
the cut: long enough not to re-power before the rail drops, but **bounded** so a
*stuck-high* pin can't wedge the gateway off forever — a deadlock caught live on
the bench, where a halted-but-still-powered Pi held GP18 high and blocked
recovery even at healthy voltage.

## Pin map (see `firmware/config.py`)

| Pico pin | To | Purpose |
|---|---|---|
| GP15 `PIN_PI_POWER_EN` | 5V buck ENABLE / high-side load switch | gate the Pi's power |
| GP16 `PIN_PSU_RELAY` | gateway PSU relay IN | second, more robust cutoff, wired through the relay's **NC** contact (COM ← solar charge controller load(+), NC → PSU(+)) so a Pico reboot fails safe to *powered*, not cut |
| GP14 `PIN_SHUTDOWN_REQ` | Pi GPIO input | "please halt now" |
| GP18 `PIN_HALT_CONFIRMED` | Pi **GPIO27** (`gpio-poweroff`) → GP18, input w/ internal pull-down | "halt complete" — cut early instead of guessing the grace period (see state machine above) |
| GP4/GP5 `PIN_I2C_SDA/SCL` | I2C0 bus: INA3221 (`0x40`) + BME280 (`0x77`) | battery voltage + enclosure temp |
| GP26/ADC0 `PIN_BATTERY_ADC` | divided battery voltage | backup if I2C fails |
| GP17 `PIN_FAN_RELAY` | enclosure fan relay IN | thermostat-controlled fan |

Note: on the **relay-only** field build, GP15's 5V-buck ENABLE has no target (the
PSU has no enable line) — power is cut solely by the GP16 relay, and GP15 is left
unconnected.

Note: GP4/GP5 were originally slated for RS485 (Modbus) to the charge controller;
the shipped firmware reads voltage over I2C instead (`ina3221.py`), so those pins
are free for the BME280 to share.

On the Pi side, an HA automation / small service watches `SHUTDOWN_REQ` (or the
MQTT shutdown request) and runs a clean `halt`; a systemd/HA job publishes the
heartbeat the Pico watches.

## Enclosure fan thermostat

The Pico also reads the enclosure's BME280 temperature and humidity (same part
as the gateway's SparkFun Environmental Combo Breakout, reimplemented for
MicroPython in [`bme280.py`](../packages/pico-watchdog/firmware/bme280.py) —
no CircuitPython/Blinka dependency; pressure is left uncompensated since
nothing here needs it) and drives a fan relay from the temperature reading.
Both readings and the fan state are published to MQTT and registered in Home
Assistant as **Cabinet Temp**, **Cabinet Humidity**, and **Cabinet Fan**.

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
| `SHUTDOWN_VOLTAGE` | 12.5 V | **request** graceful halt — Pi still has 5V headroom (must be **below** gateway software CRITICAL ≈ 12.8 V / 25% SoC) |
| `HARD_CUTOFF_VOLTAGE` | 12.0 V | **force** relay open regardless of halt state — battery-protection floor |
| `RECOVERY_VOLTAGE` | 13.2 V | safe to re-power |
| `GRACE_SECONDS` | 90 | max wait for the Pi to halt (before a forced cut) |
| `HEARTBEAT_TIMEOUT_S` | 300 | stale heartbeat → reboot (only while the Pico's own MQTT is connected) |
| `HALT_SETTLE_SECONDS` | 30 | max time an asserted `halt_confirmed` holds the gate in `PI_OFF` before voltage governs |
| `MQTT_RECONNECT_INTERVAL_S` | 30 | broker reconnect retry — must stay **well below** `HEARTBEAT_TIMEOUT_S` |
| `FAN_ON_TEMP_C` | 35.0 | enclosure temp at/above which the fan turns on |
| `FAN_OFF_TEMP_C` | 30.0 | enclosure temp at/below which the fan turns back off |

LiFePO4's flat curve makes voltage-only SoC approximate — fine for the software
tier; the gate's hard thresholds work on raw voltage. Re-tune both the thresholds
and the [`soc.py`](../packages/pico-watchdog/firmware/soc.py) curve after the
field soak.

## MQTT topics & remote log access

| Topic | Dir | Payload |
|---|---|---|
| `securitymesh/gateway/pico/telemetry` | pub, 30 s | JSON snapshot: `voltage_v`, `load_voltage_v`, `solar_voltage_v`, `soc_pct`, `gate_state`, `last_action`, `halt_confirmed`, `heartbeat_age_s`, `enclosure_temp_c`, `enclosure_humidity_pct`, `fan_on` |
| `securitymesh/gateway/pi/heartbeat` | sub | the Pi's heartbeat the watchdog monitors |
| `securitymesh/gateway/pico/log` | pub, **retained** | `{"log": "<last LOG_TAIL_LINES of watchdog.log>"}` |

**`…/pico/log` — remote access to the flash event log.** The event log
([`local_log.py`](../packages/pico-watchdog/firmware/local_log.py)) lives on the
Pico's flash and is otherwise only readable over USB. On **every MQTT
(re)connect** the Pico republishes its tail (active file + `.1` backup) to this
**retained** topic, so the `action=`/`boot` lines written *while the broker was
down* (cuts, reboots) reach the broker once the Pico is back online — no USB
cable. Retained ⇒ a subscriber gets the latest tail immediately, any time.
Cadence note: it refreshes **on reconnect**, not continuously — it's for "what
happened while it was offline," not live tailing (use `…/telemetry` for live).

### Subscribing in Home Assistant

**Ad-hoc:** Settings → Devices & Services → **MQTT** → **Configure** →
**"Listen to a topic"** → `securitymesh/gateway/pico/log` → Start. The retained
tail shows immediately.

**Persistent sensor + card.** The tail is multi-KB — over HA's 255-char *state*
limit — so it must live in an *attribute*, not the state:

```yaml
mqtt:
  sensor:
    - name: "Pico Watchdog Log"
      unique_id: pico_watchdog_log
      state_topic: "securitymesh/gateway/pico/log"
      value_template: "{{ value_json.log.strip().split('\n') | last | truncate(250, True) }}"
      json_attributes_topic: "securitymesh/gateway/pico/log"   # full tail → 'log' attribute
      icon: mdi:file-document-outline
```

```yaml
# dashboard card
type: markdown
content: |
  ## Pico Watchdog Log
  ```
  {{ state_attr('sensor.pico_watchdog_log', 'log') }}
  ```
```

State = the most recent log line (glanceable); the full tail is the `log`
attribute (Developer Tools → States, or the card above).

## OTA-triggered gateway restart

Applying an OTA update asks the gateway to halt first (`_prepare_and_apply_ota`
in `main.py`), so the Pico's own post-activation reset never lands on a live
Pi. The PSU relay's NC wiring keeps the Pi *powered* straight through that
halt — it just goes OS-down with no self-wake — and the normal stale-heartbeat
watchdog can't bring it back either: that watchdog is deliberately suppressed
whenever the Pico's own MQTT link is down (see `gate_logic.decide`'s
`mqtt_connected` gate, which exists to avoid a reboot loop when the *Pico* is
network-blind), and the link is always down right after this since the broker
runs on the same Pi that just halted.

Worse, the gateway actually halts (`{"cmd": "prepare_shutdown"}`) the moment
that command is sent — unconditionally, regardless of what `main.py` decides
afterwards. So it's not just the success path that needs recovering: an OTA
that times out waiting for `halt_confirmed` and **aborts** (leaving old
firmware in place, by design) still leaves a genuinely halted gateway behind.
This was found live: `halt_confirmed` never tripped on a real halt, the OTA
aborted after its 120s timeout, and the gateway sat powered-but-halted with
nothing watching, until it was power-cycled by hand.

`_prepare_and_apply_ota` closes this by pulsing the PSU relay itself — once,
directly — right after the wait loop resolves, before branching into
`activate_staged()` or logging the abort. That covers proceed, abort, and an
`activate_staged()` failure in one place, with no dependency on the Pico
itself resetting. It's a one-shot action tied to a specific, self-triggered
event (not an ongoing "is it actually hung?" guess), so it can't turn into
the reboot loop the heartbeat watchdog's `mqtt_connected` guard exists to
prevent.

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
