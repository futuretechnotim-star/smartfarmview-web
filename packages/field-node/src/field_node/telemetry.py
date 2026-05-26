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
    usage = shutil.disk_usage(settings.capture_dir)
    return round(usage.used / usage.total * 100, 1)


class TelemetryPublisher:
    def __init__(self) -> None:
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.node_id)
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._connected = False

    def connect(self) -> None:
        self._client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self._client.loop_start()

    def _on_connect(self, client: mqtt.Client, userdata: object, flags: object, rc: int, properties: object = None) -> None:
        self._connected = True
        log.info("mqtt_connected", host=settings.mqtt_host, port=settings.mqtt_port)

    def _on_disconnect(self, client: mqtt.Client, userdata: object, flags: object, rc: int, properties: object = None) -> None:
        self._connected = False
        log.warning("mqtt_disconnected", rc=rc)

    def _topic(self, key: str) -> str:
        return f"securitymesh/{settings.node_id}/{key}"

    def publish(self, key: str, payload: object) -> None:
        if not self._connected:
            log.warning("mqtt_not_connected_skipping", key=key)
            return
        self._client.publish(self._topic(key), json.dumps(payload), qos=1, retain=False)

    def publish_heartbeat(self) -> None:
        self.publish("telemetry", {
            "ts": time.time(),
            "cpu_temp": _cpu_temp(),
            "storage_pct": _storage_percent(),
        })

    def publish_motion_event(self, snapshot_path: str) -> None:
        self.publish("motion", {
            "ts": time.time(),
            "snapshot": snapshot_path,
        })

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
