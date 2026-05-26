import json
import shutil
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import structlog

from field_node.config import settings

log = structlog.get_logger()


def _cpu_temp() -> float:
    try:
        return float(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000.0
    except Exception:
        return -1.0


def _storage_percent() -> float:
    try:
        usage = shutil.disk_usage(settings.capture_dir)
        return round(usage.used / usage.total * 100, 1)
    except Exception:
        return -1.0


def _device_info() -> dict[str, object]:
    return {
        "identifiers": [settings.node_id],
        "name": settings.node_id,
        "model": "SecurityMesh Field Node",
        "manufacturer": "SmartFarmView",
    }


class TelemetryPublisher:
    def __init__(self) -> None:
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.node_id)  # type: ignore[attr-defined]
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect  # type: ignore[assignment]
        self._connected = False

    def connect(self) -> None:
        self._client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self._client.loop_start()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: object,
        rc: int,
        properties: object = None,
    ) -> None:
        self._connected = True
        log.info("mqtt_connected", host=settings.mqtt_host, port=settings.mqtt_port)
        self.publish_discovery()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: object,
        rc: int,
        properties: object = None,
    ) -> None:
        self._connected = False
        log.warning("mqtt_disconnected", rc=rc)

    def _topic(self, key: str) -> str:
        return f"securitymesh/{settings.node_id}/{key}"

    def _discovery_topic(self, component: str, object_id: str) -> str:
        prefix = settings.mqtt_discovery_prefix
        return f"{prefix}/{component}/{settings.node_id}/{object_id}/config"

    def _publish_raw(self, topic: str, payload: object, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload), qos=1, retain=retain)

    def publish(self, key: str, payload: object) -> None:
        if not self._connected:
            log.warning("mqtt_not_connected_skipping", key=key)
            return
        self._client.publish(self._topic(key), json.dumps(payload), qos=1, retain=False)

    def publish_discovery(self) -> None:
        node = settings.node_id
        telemetry_topic = self._topic("telemetry")
        motion_state_topic = self._topic("motion_state")
        device = _device_info()

        entities = [
            (
                "sensor",
                "cpu_temp",
                {
                    "name": "CPU Temperature",
                    "unique_id": f"{node}_cpu_temp",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.cpu_temp }}",
                    "unit_of_measurement": "°C",
                    "device_class": "temperature",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                "sensor",
                "storage_pct",
                {
                    "name": "Storage Used",
                    "unique_id": f"{node}_storage_pct",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.storage_pct }}",
                    "unit_of_measurement": "%",
                    "state_class": "measurement",
                    "icon": "mdi:micro-sd",
                    "device": device,
                },
            ),
            (
                "binary_sensor",
                "motion",
                {
                    "name": "Motion",
                    "unique_id": f"{node}_motion",
                    "state_topic": motion_state_topic,
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "device_class": "motion",
                    "device": device,
                },
            ),
        ]

        for component, object_id, config in entities:
            topic = self._discovery_topic(component, object_id)
            self._publish_raw(topic, config, retain=True)
            log.info("discovery_published", component=component, object_id=object_id)

    def publish_heartbeat(self) -> None:
        self.publish(
            "telemetry",
            {
                "ts": time.time(),
                "cpu_temp": _cpu_temp(),
                "storage_pct": _storage_percent(),
            },
        )

    def publish_motion_event(self, snapshot_path: str) -> None:
        self.publish("motion_state", "ON")
        self.publish(
            "motion",
            {
                "ts": time.time(),
                "snapshot": snapshot_path,
            },
        )

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
