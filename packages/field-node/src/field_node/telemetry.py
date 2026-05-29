import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import paho.mqtt.client as mqtt
import structlog

from field_node.config import settings

if TYPE_CHECKING:
    from field_node.power.base import PowerReading

log = structlog.get_logger()

CommandHandler = Callable[[dict[str, object]], None]


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
    def __init__(self, on_command: CommandHandler | None = None) -> None:
        self._on_command = on_command
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.node_id)  # type: ignore[attr-defined]
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect  # type: ignore[assignment]
        self._client.on_message = self._on_message
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
        self._client.subscribe(self._topic("cmd"), qos=1)
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

    def _on_message(self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload)
            if self._on_command:
                self._on_command(payload)
        except Exception as e:
            log.warning("cmd_parse_error", error=str(e))

    def _topic(self, key: str) -> str:
        return f"securitymesh/{settings.node_id}/{key}"

    def _discovery_topic(self, component: str, object_id: str) -> str:
        return f"{settings.mqtt_discovery_prefix}/{component}/{settings.node_id}/{object_id}/config"

    def _publish_raw(self, topic: str, payload: object, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload), qos=1, retain=retain)

    def publish(self, key: str, payload: object) -> None:
        if not self._connected:
            log.warning("mqtt_not_connected_skipping", key=key)
            return
        self._client.publish(self._topic(key), json.dumps(payload), qos=1, retain=False)

    def publish_snapshot(self, jpeg_bytes: bytes) -> None:
        if not self._connected:
            log.warning("mqtt_not_connected_skipping", key="snapshot")
            return
        self._client.publish(self._topic("snapshot"), jpeg_bytes, qos=1, retain=True)
        log.info("snapshot_published", size_kb=round(len(jpeg_bytes) / 1024, 1))

    def publish_discovery(self) -> None:
        node = settings.node_id
        telemetry_topic = self._topic("telemetry")
        motion_state_topic = self._topic("motion_state")
        snapshot_topic = self._topic("snapshot")
        cmd_topic = self._topic("cmd")
        device = _device_info()

        entities: list[tuple[str, str, dict[str, object]]] = [
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
            (
                "sensor",
                "battery_voltage",
                {
                    "name": "Battery Voltage",
                    "unique_id": f"{node}_battery_voltage",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.battery_voltage }}",
                    "unit_of_measurement": "V",
                    "device_class": "voltage",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                "sensor",
                "battery_current",
                {
                    "name": "Battery Current",
                    "unique_id": f"{node}_battery_current",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.battery_current_ma }}",
                    "unit_of_measurement": "mA",
                    "device_class": "current",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                "sensor",
                "battery_power",
                {
                    "name": "Battery Power",
                    "unique_id": f"{node}_battery_power",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.battery_power_mw }}",
                    "unit_of_measurement": "mW",
                    "device_class": "power",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                "sensor",
                "battery_soc",
                {
                    "name": "Battery Level",
                    "unique_id": f"{node}_battery_soc",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.battery_soc_pct }}",
                    "unit_of_measurement": "%",
                    "device_class": "battery",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                "sensor",
                "battery_runtime",
                {
                    "name": "Battery Runtime",
                    "unique_id": f"{node}_battery_runtime",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.battery_runtime_hours | default('unavailable') }}",
                    "unit_of_measurement": "h",
                    "icon": "mdi:timer-outline",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                "sensor",
                "power_mode",
                {
                    "name": "Power Mode",
                    "unique_id": f"{node}_power_mode",
                    "state_topic": telemetry_topic,
                    "value_template": "{{ value_json.power_mode | default('unknown') }}",
                    "icon": "mdi:battery-heart-variant",
                    "device": device,
                },
            ),
            (
                "camera",
                "snapshot",
                {
                    "name": "Camera",
                    "unique_id": f"{node}_camera",
                    "topic": snapshot_topic,
                    "device": device,
                },
            ),
            (
                "button",
                "capture",
                {
                    "name": "Capture Snapshot",
                    "unique_id": f"{node}_capture_btn",
                    "command_topic": cmd_topic,
                    "payload_press": json.dumps({"cmd": "capture"}),
                    "icon": "mdi:camera",
                    "device": device,
                },
            ),
        ]

        for component, object_id, config in entities:
            topic = self._discovery_topic(component, object_id)
            self._publish_raw(topic, config, retain=True)
            log.info("discovery_published", component=component, object_id=object_id)

    def publish_heartbeat(
        self,
        power: "PowerReading | None" = None,
        power_mode: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "ts": time.time(),
            "cpu_temp": _cpu_temp(),
            "storage_pct": _storage_percent(),
        }
        if power is not None:
            payload["battery_voltage"] = power.voltage_v
            payload["battery_current_ma"] = power.current_ma
            payload["battery_power_mw"] = power.power_mw
            payload["battery_discharging"] = power.is_discharging
            payload["battery_soc_pct"] = power.soc_pct
            runtime = power.runtime_hours(settings.battery_capacity_mah)
            if runtime is not None:
                payload["battery_runtime_hours"] = runtime
        if power_mode is not None:
            payload["power_mode"] = power_mode
        self.publish("telemetry", payload)

    def publish_motion_event(self, snapshot_path: str) -> None:
        self.publish("motion_state", "ON")
        self.publish(
            "motion",
            {
                "ts": time.time(),
                "snapshot": snapshot_path,
            },
        )

    def publish_motion_clear(self) -> None:
        self.publish("motion_state", "OFF")

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
