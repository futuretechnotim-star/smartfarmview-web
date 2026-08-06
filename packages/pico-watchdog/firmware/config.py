"""Pico 2 W watchdog configuration.

Non-secret defaults live here; WiFi/MQTT credentials live in ``secrets.py``
(git-ignored, copied onto the Pico at flash time). All thresholds are for the
EcoWorthy 12.8V 20Ah LiFePO4 baseline battery — re-tune after the field-soak
dataset (see docs/gateway-node.md).
"""

# --- Power-gate thresholds (Volts) ------------------------------------------
# LiFePO4 rests very flat (~13.0-13.3V most of its range), so use conservative
# points. shutdown_voltage MUST be below the gateway software CRITICAL level so
# Home Assistant halts gracefully first; the gate is the backstop.
# Two-stage low-voltage handling (see gate_logic.decide):
#  - at SHUTDOWN_VOLTAGE, request a graceful halt while the Pi still has 5V
#    headroom — the 12→5V buck browns out near its dropout, so this sits well
#    above the floor (bench test: the Pi lost its network by ~12.1-12.2V input);
#  - at HARD_CUTOFF_VOLTAGE, force the relay open regardless of halt state to
#    stop deep-discharging the pack.
# Both sit BELOW the gateway software's CRITICAL level (~25% SoC ≈ 12.8V), so
# Home Assistant sheds load / halts first and the gate is only the backstop.
SHUTDOWN_VOLTAGE = 12.5  # request graceful shutdown (backstop below HA CRITICAL)
HARD_CUTOFF_VOLTAGE = 12.0  # force relay open — battery-protection floor
RECOVERY_VOLTAGE = 13.2  # charging well underway → safe to re-power the Pi
GRACE_SECONDS = 90  # max time to wait for the Pi to halt after SHUTDOWN_REQ
HEARTBEAT_TIMEOUT_S = 300  # no Pi heartbeat for this long (while ON) → reboot
# How long an asserted halt_confirmed may hold the gate in PI_OFF before voltage
# takes over. A de-energized Pi drops gpio-poweroff in ~1-2s; anything longer
# means the pin is stuck high (e.g. a back-power path the relay doesn't break),
# and must not wedge the gateway off forever. See gate_logic.decide (PI_OFF).
HALT_SETTLE_SECONDS = 30

# --- GPIO pin map (BCM/GP numbering on the Pico) ----------------------------
PIN_PI_POWER_EN = 15  # drives the 5V buck ENABLE / high-side load switch
PIN_SHUTDOWN_REQ = 14  # asserted high to ask the Pi to begin a clean shutdown
PIN_HALT_CONFIRMED = 18  # from the Pi's gpio-poweroff overlay: halt actually
# completed (not merely requested) — active HIGH, with
# an internal pull-down so a disconnected/loose wire
# fails safe to "not halted" rather than forcing a cut.
PIN_BATTERY_ADC = 26  # ADC0 — divided battery voltage (fallback if I2C fails)

# I2C0 — shared bus: INA3221 voltage sensor + BME280 enclosure temperature
PIN_I2C_SDA = 4  # GP4 = I2C0 SDA
PIN_I2C_SCL = 5  # GP5 = I2C0 SCL
I2C_FREQ = 400_000
INA3221_ADDR = 0x40  # ADDR pin → GND (default on most breakout boards)
BME280_ADDR = 0x77  # SDO tied high (SparkFun Environmental Combo Breakout)

# --- Fan relay ----------------------------------------------------------------
PIN_FAN_RELAY = 17  # drives the enclosure fan relay

# --- Gateway PSU relay ---------------------------------------------------------
# A second, more robust cutoff on the PSU itself (vs. PIN_PI_POWER_EN, which only
# gates the 5V buck enable). Mirrors the same gate_logic actions in lockstep —
# see main.py's action-handling block.
PIN_PSU_RELAY = 16

# --- Fan thermostat (hysteresis, degrees C) ---------------------------------
FAN_ON_TEMP_C = 35.0  # enclosure temp at/above which the fan turns on
FAN_OFF_TEMP_C = 30.0  # enclosure temp at/below which the fan turns back off

# Channel assignments (both IN+ and IN- shorted together for voltage-only on CH1/CH3;
# CH2 is in-line through the 0.1 Ω onboard shunt for 5V load current up to 1.638 A)
INA3221_CH_BATTERY = 1  # CH1: battery 12V rail (voltage only)
INA3221_CH_LOAD_5V = 2  # CH2: 5V buck output → Pi 5 (voltage + current ≤ 1.638 A)
INA3221_CH_SOLAR = 3  # CH3: solar panel terminals (voltage only)
INA3221_CHANNEL = INA3221_CH_BATTERY  # primary channel used by gate_logic.py

# ADC fallback: voltage divider ratio (Vbattery / Vadc).
# Default assumes 100 kΩ / 10 kΩ divider → ratio 11.0 (12 V → 1.09 V).
# Adjust if different resistors are fitted.
ADC_DIVIDER_RATIO = 11.0
ADC_REF_VOLTAGE = 3.3

# --- MQTT topics ------------------------------------------------------------
TELEMETRY_TOPIC = b"securitymesh/gateway/pico/telemetry"
# Retained tail of watchdog.log, republished on every MQTT (re)connect so the
# events logged while the broker was down (cuts/reboots) reach HA without USB.
LOG_TOPIC = b"securitymesh/gateway/pico/log"
LOG_TAIL_LINES = 40
HEARTBEAT_TOPIC = b"securitymesh/gateway/pi/heartbeat"
COMMAND_TOPIC = b"securitymesh/gateway/pico/cmd"
GATEWAY_CMD_TOPIC = b"securitymesh/sfv-gateway/cmd"  # gateway_node's own command topic

# --- Loop timing ------------------------------------------------------------
LOOP_INTERVAL_S = 5  # how often to sample voltage and run the state machine
TELEMETRY_INTERVAL_S = 30  # how often to publish telemetry
WIFI_RECONNECT_INTERVAL_S = 60  # how often to retry WiFi after a failed/dropped connection
# Must be well below HEARTBEAT_TIMEOUT_S: the heartbeat arrives over MQTT, so if
# reconnect is as slow as the heartbeat timeout the Pico can time out and reboot
# the Pi before it ever gets back on the broker (a ~5-min reboot loop). 30s gives
# many reconnect attempts inside one heartbeat window.
MQTT_RECONNECT_INTERVAL_S = 30  # how often to retry MQTT after a dropped connection
# PI_ON but no MQTT for this long → assume a wedged radio the reconnect (even
# with a radio bounce) can't clear, and machine.reset() to self-heal. Generous
# so it never thrashes; only fires while PI_ON (a Pico reset there keeps the Pi
# powered via the NC relay). Used when the Pico's own wifi is ALSO down — a
# stronger signal something is actually wrong, not just a quiet broker.
NET_RECOVERY_TIMEOUT_WIFI_DOWN_S = 900
# wifi up but MQTT down for this long → same self-heal, longer leash. Found
# live 2026-08-06: the 900s timeout fired ~11 min into a routine boot (HA
# Supervisor + several Docker add-ons still coming up) and needlessly
# power-cycled an otherwise-healthy Pi. wifi being up means the Pico's own
# radio/AP link is proven fine, so a quiet broker is far more likely a
# software-side hiccup (HA update, Mosquitto add-on restarting) than a hung
# Pi — worth waiting out before a disruptive power-cycle.
NET_RECOVERY_TIMEOUT_WIFI_UP_S = 1800

# --- Local log ---------------------------------------------------------------
# Bounded on-flash event log for troubleshooting when MQTT is unreachable —
# see local_log.py. Two files of this size are kept (active + one rotated
# backup), so total flash usage is bounded to ~2x this value.
LOCAL_LOG_PATH = "watchdog.log"
LOCAL_LOG_MAX_BYTES = 16384

# --- OTA -----------------------------------------------------------------------
# An OTA update ends in machine.reset(), which briefly leaves the relay control
# pins undriven — until the relay's own wiring makes that fail-safe, an update
# must wait for the gateway to actually halt first (see ota_prep.py).
OTA_SHUTDOWN_TIMEOUT_S = 120  # max wait for halt_confirmed before aborting the update
