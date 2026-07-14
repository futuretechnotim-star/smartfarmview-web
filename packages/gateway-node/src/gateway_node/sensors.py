"""Gateway environmental sensor service (gateway-sensors.service).

Polls the SparkFun Environmental Combo Breakout (CCS811 @ 0x5B + BME280 @ 0x77)
on I2C bus 1 every 60 seconds and publishes readings to MQTT.

Publishes HA MQTT discovery on startup so all five sensors appear under the
SFV Gateway device automatically:
  - Temperature (°C)
  - Relative Humidity (%)
  - Pressure (hPa)
  - eCO2 (ppm)   — shows 0 during CCS811 warm-up (~20 min from cold start)
  - TVOC (ppb)   — same warm-up caveat

Topic: securitymesh/{node_id}/sensors  (retained JSON)
"""

from __future__ import annotations

import json
import signal
import time

import board
import busio
import adafruit_bme280.basic as adafruit_bme280
import adafruit_ccs811
import paho.mqtt.client as mqtt
import structlog

from gateway_node.config import settings

log = structlog.get_logger()

_running = True
_POLL_INTERVAL = 60  # seconds
_CCS811_ADDR = 0x5B  # SparkFun board has ADDR pin high


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    _running = False


def _gateway_device() -> dict[str, object]:
    return {
        "identifiers": [settings.node_id],
        "name": "SFV Gateway",
        "model": "Pi 5 Gateway Node",
        "manufacturer": "SmartFarmView",
    }


def _publish_discovery(client: mqtt.Client) -> None:
    node = settings.node_id
    prefix = settings.mqtt_discovery_prefix
    state_topic = f"securitymesh/{node}/sensors"
    device = _gateway_device()

    entities: list[tuple[str, dict[str, object]]] = [
        ("temperature", {
            "name": "Temperature",
            "unique_id": f"{node}_temperature",
            "state_topic": state_topic,
            "value_template": "{{ value_json.temperature | round(1) }}",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "device": device,
        }),
        ("humidity", {
            "name": "Humidity",
            "unique_id": f"{node}_humidity",
            "state_topic": state_topic,
            "value_template": "{{ value_json.humidity | round(1) }}",
            "unit_of_measurement": "%",
            "device_class": "humidity",
            "state_class": "measurement",
            "device": device,
        }),
        ("pressure", {
            "name": "Pressure",
            "unique_id": f"{node}_pressure",
            "state_topic": state_topic,
            "value_template": "{{ value_json.pressure | round(1) }}",
            "unit_of_measurement": "hPa",
            "device_class": "pressure",
            "state_class": "measurement",
            "entity_category": "diagnostic",
            "device": device,
        }),
        ("eco2", {
            "name": "eCO2",
            "unique_id": f"{node}_eco2",
            "state_topic": state_topic,
            "value_template": "{{ value_json.eco2 }}",
            "unit_of_measurement": "ppm",
            "device_class": "carbon_dioxide",
            "state_class": "measurement",
            "device": device,
        }),
        ("tvoc", {
            "name": "TVOC",
            "unique_id": f"{node}_tvoc",
            "state_topic": state_topic,
            "value_template": "{{ value_json.tvoc }}",
            "unit_of_measurement": "ppb",
            "device_class": "volatile_organic_compounds_parts",
            "state_class": "measurement",
            "device": device,
        }),
    ]

    for object_id, config in entities:
        topic = f"{prefix}/sensor/{node}/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)
        log.info("discovery_published", object_id=object_id)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── I2C sensors ───────────────────────────────────────────────────────────
    i2c = busio.I2C(board.SCL, board.SDA)
    bme = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x77)
    ccs = adafruit_ccs811.CCS811(i2c, address=_CCS811_ADDR)
    log.info("sensors_initialised", bme280="0x77", ccs811=hex(_CCS811_ADDR))

    # ── MQTT ─────────────────────────────────────────────────────────────────
    client: mqtt.Client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{settings.node_id}-sensors",
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(c: mqtt.Client, userdata: object, flags: object, rc: object, props: object = None) -> None:
        log.info("mqtt_connected")
        _publish_discovery(c)

    client.on_connect = on_connect
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    client.loop_start()

    state_topic = f"securitymesh/{settings.node_id}/sensors"
    last_poll = 0.0

    try:
        while _running:
            now = time.monotonic()
            if now - last_poll >= _POLL_INTERVAL:
                try:
                    payload: dict[str, object] = {
                        "temperature": round(bme.temperature, 2),
                        "humidity": round(bme.relative_humidity, 2),
                        "pressure": round(bme.pressure, 2),
                        "eco2": ccs.eco2 if ccs.data_ready else 0,
                        "tvoc": ccs.tvoc if ccs.data_ready else 0,
                        "ccs811_ready": ccs.data_ready,
                    }
                    client.publish(state_topic, json.dumps(payload), retain=True)
                    log.info(
                        "sensors_published",
                        temp=payload["temperature"],
                        humidity=payload["humidity"],
                        pressure=payload["pressure"],
                        eco2=payload["eco2"],
                        tvoc=payload["tvoc"],
                        ccs811_ready=payload["ccs811_ready"],
                    )
                except Exception as e:
                    log.warning("sensor_read_error", error=str(e))
                last_poll = now

            time.sleep(1)
    finally:
        client.loop_stop()
        client.disconnect()
        log.info("sensors_service_stopped")
