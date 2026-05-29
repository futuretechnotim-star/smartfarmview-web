# Field Node Sensors

## HC-SR501 PIR Motion Sensor

**Module:** HiLetgo HC-SR501

### Wiring

| HC-SR501 pin | Pi Zero 2 W | Notes |
|---|---|---|
| VCC | 5V (pin 2 or 4) | Via solar HAT passthrough |
| GND | GND (pin 6, 9, etc.) | Via solar HAT passthrough |
| OUT | GPIO 26 / pin 37 | BCM numbering |

> The OUT signal is 3.3V HIGH on motion — safe to connect directly to the Pi GPIO without a level shifter.

### Jumper settings

| Jumper | Setting | Effect |
|---|---|---|
| Trigger mode | **H** (repeat) | OUT stays HIGH while motion continues; recommended |
| Sensitivity | ~half | Adjust to taste on deployment |
| Delay | ~5 s | Time OUT stays HIGH after last motion |

### Warm-up

The HC-SR501 requires ~60 seconds after power-on to stabilise before its output is reliable. The driver suppresses all motion events during this window. The `pir_warmup_seconds` config value (default 60) controls the window length — do not reduce it below the sensor's stated warm-up time.

### Config

Set in `/opt/field-node/.env` or via environment variables:

```
FIELD_NODE_PIR_GPIO_PIN=26       # BCM pin number (default 26, physical pin 37)
FIELD_NODE_PIR_WARMUP_SECONDS=60 # warm-up suppression window
```

### Verify wiring independently

Before starting the full service, use the standalone test script on the Pi:

```bash
cd /opt/field-node
.venv/bin/python3 scripts/test_pir.py
```

Wait for the warm-up countdown, then walk in front of the sensor. You should see `MOTION DETECTED` and `motion cleared` logged. Ctrl+C to exit.

### Home Assistant integration

On motion, the field node:
1. Captures a JPEG snapshot
2. Publishes the image to the HA snapshot camera entity
3. Sets the `Motion` binary sensor to **ON** via MQTT

When motion clears, the `Motion` binary sensor is set to **OFF**.

All entities are registered automatically via MQTT discovery on service start — no manual HA configuration needed.
