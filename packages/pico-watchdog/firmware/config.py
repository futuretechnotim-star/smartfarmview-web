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
SHUTDOWN_VOLTAGE = 12.0  # ~low SoC under light load → request shutdown then cut
RECOVERY_VOLTAGE = 13.2  # charging well underway → safe to re-power the Pi
GRACE_SECONDS = 90  # max time to wait for the Pi to halt after SHUTDOWN_REQ
HEARTBEAT_TIMEOUT_S = 300  # no Pi heartbeat for this long (while ON) → reboot

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
HEARTBEAT_TOPIC = b"securitymesh/gateway/pi/heartbeat"
COMMAND_TOPIC = b"securitymesh/gateway/pico/cmd"
GATEWAY_CMD_TOPIC = b"securitymesh/sfv-gateway/cmd"  # gateway_node's own command topic

# --- Loop timing ------------------------------------------------------------
LOOP_INTERVAL_S = 5  # how often to sample voltage and run the state machine
TELEMETRY_INTERVAL_S = 30  # how often to publish telemetry
WIFI_RECONNECT_INTERVAL_S = 60  # how often to retry WiFi after a failed/dropped connection
MQTT_RECONNECT_INTERVAL_S = 300  # how often to retry MQTT after a dropped connection

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
