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
PIN_BATTERY_ADC = 26  # ADC0 — divided battery voltage (backup if RS485 fails)
PIN_RS485_TX = 4  # UART1 TX → MAX485 DI
PIN_RS485_RX = 5  # UART1 RX ← MAX485 RO
PIN_RS485_DE = 6  # MAX485 DE/RE (high = transmit, low = receive)

# Battery ADC divider ratio (Vbattery / Vadc) — set to match your resistor pair.
ADC_DIVIDER_RATIO = 11.0
ADC_REF_VOLTAGE = 3.3

# --- RS485 / Modbus (ECO-WORTHY PWM controller) -----------------------------
RS485_BAUD = 9600
MODBUS_SLAVE_ID = 1
MODBUS_READ_ADDR = 0  # holding-register base; read a block and decode
MODBUS_READ_COUNT = 16

# --- MQTT topics ------------------------------------------------------------
TELEMETRY_TOPIC = b"securitymesh/gateway/pico/telemetry"
HEARTBEAT_TOPIC = b"securitymesh/gateway/pi/heartbeat"
COMMAND_TOPIC = b"securitymesh/gateway/pico/cmd"

# --- Loop timing ------------------------------------------------------------
LOOP_INTERVAL_S = 5  # how often to sample voltage and run the state machine
TELEMETRY_INTERVAL_S = 30  # how often to publish telemetry
