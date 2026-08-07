"""Gateway power-brain service entrypoint.

Connects to the local Mosquitto broker, consumes battery/solar telemetry
published by the Pico 2 W watchdog, drives the gateway power state machine, and:

  * publishes power mode + solar projection to MQTT
  * publishes a periodic heartbeat the Pico's hardware watchdog monitors
  * tracks connected field nodes via their telemetry publishes (field nodes have
    no separate heartbeat topic) and registers their cameras as MQTT camera
    entities in Home Assistant automatically
  * publishes the count of currently-online field nodes

Discovery is published unconditionally on every MQTT connect — not gated on
any optional hardware.
"""

from __future__ import annotations

import json
import signal
import time
from typing import Any

import paho.mqtt.client as mqtt
import structlog
from power_policy import PowerMode

from gateway_node.config import settings
from gateway_node.ha_client import request_shutdown
from gateway_node.power import GatewayPowerManager

log = structlog.get_logger()
_running = True

# Field nodes are considered offline if no heartbeat received within this window.
_NODE_TIMEOUT_SECONDS = 300  # 5 minutes


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    log.info("shutdown_signal_received", signum=signum)
    _running = False


def _build_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{settings.node_id}-power")  # type: ignore[attr-defined]
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    return client


def _publish_power_state(client: mqtt.Client, manager: GatewayPowerManager) -> None:
    status = manager.solar_status
    payload = {
        "mode": manager.mode.value,
        "camera_enabled": manager.camera_enabled,
        "is_daytime": status.is_daytime,
        "net_avg_ma": status.net_avg_ma,
        "projected_eod_soc": status.projected_eod_soc,
        "deficit_pct": status.deficit_pct,
        "mode_reason": status.mode_reason,
    }
    client.publish(f"securitymesh/{settings.node_id}/power", json.dumps(payload), retain=True)


def _gateway_device() -> dict[str, object]:
    return {
        "identifiers": [settings.node_id],
        "name": "SFV Gateway",
        "model": "Pi 5 Gateway Node",
        "manufacturer": "SmartFarmView",
    }


def _publish_gateway_discovery(client: mqtt.Client) -> None:
    """Register all gateway power/battery/node-count entities in HA."""
    node = settings.node_id
    prefix = settings.mqtt_discovery_prefix
    device = _gateway_device()
    pico_topic = settings.pico_telemetry_topic
    power_topic = f"securitymesh/{node}/power"
    nodes_topic = f"securitymesh/{node}/nodes/online"

    entities: list[tuple[str, str, dict[str, object]]] = [
        # ── Power brain ──────────────────────────────────────────────────────
        (
            "select",
            "power_mode",
            {
                "name": "Power Mode",
                "unique_id": f"{node}_power_mode",
                "state_topic": power_topic,
                "value_template": "{{ value_json.mode }}",
                "options": ["NORMAL", "ECO", "LOW", "CRITICAL"],
                "icon": "mdi:solar-power",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        # ── Pico telemetry (value_template extracts fields from JSON blob) ───
        (
            "sensor",
            "soc_pct",
            {
                "name": "Battery SoC",
                "unique_id": f"{node}_soc_pct",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.soc_pct }}",
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "measurement",
                "device": device,
            },
        ),
        (
            "sensor",
            "voltage",
            {
                "name": "Battery Voltage",
                "unique_id": f"{node}_voltage",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.voltage_v | round(2) }}",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "suggested_display_precision": 2,
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "sensor",
            "load_voltage",
            {
                "name": "5V Load Voltage",
                "unique_id": f"{node}_load_voltage",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.load_voltage_v | round(2) }}",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "suggested_display_precision": 2,
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "sensor",
            "solar_voltage",
            {
                "name": "Solar Voltage",
                "unique_id": f"{node}_solar_voltage",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.solar_voltage_v | round(2) }}",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "state_class": "measurement",
                "suggested_display_precision": 2,
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "sensor",
            "cabinet_temp",
            {
                "name": "Cabinet Temp",
                "unique_id": f"{node}_cabinet_temp",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.enclosure_temp_c }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "device": device,
            },
        ),
        (
            "sensor",
            "cabinet_humidity",
            {
                "name": "Cabinet Humidity",
                "unique_id": f"{node}_cabinet_humidity",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.enclosure_humidity_pct }}",
                "unit_of_measurement": "%",
                "device_class": "humidity",
                "state_class": "measurement",
                "device": device,
            },
        ),
        (
            "sensor",
            "pico_wifi_signal",
            {
                "name": "Pico WiFi Signal",
                "unique_id": f"{node}_pico_wifi_signal",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.wifi_rssi_dbm }}",
                "unit_of_measurement": "dBm",
                "device_class": "signal_strength",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "sensor",
            "pico_fw_version",
            {
                "name": "Pico Firmware Version",
                "unique_id": f"{node}_pico_fw_version",
                "state_topic": pico_topic,
                "value_template": "{{ value_json.fw_version }}",
                "icon": "mdi:chip",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "binary_sensor",
            "cabinet_fan",
            {
                "name": "Cabinet Fan",
                "unique_id": f"{node}_cabinet_fan",
                "state_topic": pico_topic,
                "value_template": "{{ 'ON' if value_json.fan_on else 'OFF' }}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "running",
                "device": device,
            },
        ),
        # ── Field node fleet ─────────────────────────────────────────────────
        (
            "sensor",
            "field_nodes_online",
            {
                "name": "Field Nodes Online",
                "unique_id": f"{node}_field_nodes_online",
                "state_topic": nodes_topic,
                "unit_of_measurement": "nodes",
                "icon": "mdi:access-point-network",
                "state_class": "measurement",
                "device": device,
            },
        ),
    ]

    for component, object_id, config in entities:
        topic = f"{prefix}/{component}/{node}/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)
        log.info("discovery_published", component=component, object_id=object_id)


def _publish_field_node_camera(client: mqtt.Client, node_id: str) -> None:
    """Register a field node's snapshot camera in HA when it first comes online."""
    prefix = settings.mqtt_discovery_prefix
    topic = f"{prefix}/camera/{node_id}/snapshot/config"
    payload = {
        "name": f"{node_id} Camera",
        "unique_id": f"{node_id}_snapshot",
        "topic": f"securitymesh/{node_id}/snapshot",
        "device": {
            "identifiers": [node_id],
            "name": node_id,
            "model": "SecurityMesh Field Node",
            "manufacturer": "SmartFarmView",
        },
    }
    client.publish(topic, json.dumps(payload), qos=1, retain=True)
    log.info("field_node_camera_registered", node_id=node_id)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("gateway_power_starting", node_id=settings.node_id)
    manager = GatewayPowerManager()
    client = _build_client()

    _shutdown_requested = False
    _field_nodes: dict[str, float] = {}  # node_id → last heartbeat monotonic time
    _registered_nodes: set[str] = set()  # nodes whose cameras are registered in HA

    def on_connect(c: mqtt.Client, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
        log.info("mqtt_connected", host=settings.mqtt_host, port=settings.mqtt_port)
        c.subscribe(settings.pico_telemetry_topic)
        c.subscribe(settings.pico_log_topic)
        c.subscribe(f"securitymesh/{settings.node_id}/cmd")
        # Field nodes have no dedicated heartbeat topic — telemetry.publish_heartbeat()
        # (field_node/telemetry.py) actually publishes onto the regular "telemetry"
        # topic, so that doubles as the presence signal here.
        c.subscribe("securitymesh/+/telemetry")  # track field node presence
        _publish_gateway_discovery(c)
        # Publish initial power state so HA shows something before Pico connects
        _publish_power_state(c, manager)

    def on_message(c: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        nonlocal _shutdown_requested

        # ── Capture / maintenance commands ───────────────────────────────────
        if msg.topic == f"securitymesh/{settings.node_id}/cmd":
            try:
                data = json.loads(msg.payload.decode())
                if data.get("cmd") == "capture":
                    log.info("capture_command_received")
                elif data.get("cmd") == "prepare_shutdown":
                    # Deliberate maintenance halt (e.g. before a Pico OTA reboot
                    # that would otherwise glitch gateway power) — a real halt
                    # via request_shutdown(), independent of battery/power mode.
                    log.info("prepare_shutdown_command_received")
                    request_shutdown()
            except Exception as e:
                log.warning("cmd_parse_error", error=str(e))
            return

        # ── Pico log tail (retained, republished on every Pico MQTT reconnect) ──
        # Re-logged here (not just relayed) so it lands in gateway-power's own
        # structlog output — which systemd persists to the journal — giving a
        # single, durable place to see Pico history without a separate MQTT
        # "listen to topic" session. See docs/pico-watchdog.md.
        if msg.topic == settings.pico_log_topic:
            try:
                data = json.loads(msg.payload.decode())
                log.info("pico_log_forwarded", log=data.get("log", ""))
            except (ValueError, TypeError) as e:
                log.warning("pico_log_parse_error", error=str(e))
            return

        # ── Field node telemetry (doubles as presence/heartbeat) ─────────────────
        if msg.topic.endswith("/telemetry") and msg.topic != settings.pico_telemetry_topic:
            parts = msg.topic.split("/")
            if len(parts) == 3:
                node_id = parts[1]
                if node_id == settings.node_id:
                    return  # ignore own telemetry
                _field_nodes[node_id] = time.monotonic()
                if node_id not in _registered_nodes:
                    _registered_nodes.add(node_id)
                    _publish_field_node_camera(c, node_id)
            return

        # ── Pico telemetry ────────────────────────────────────────────────────
        try:
            data = json.loads(msg.payload.decode())
        except (ValueError, TypeError) as e:
            log.warning("pico_telemetry_parse_error", error=str(e), payload=msg.payload[:200])
            return

        # OTA status updates share this topic but aren't full telemetry —
        # they carry no soc_pct, so log them distinctly instead of erroring.
        if "ota_status" in data:
            log.info("pico_ota_status", **data)
            return

        try:
            soc_pct = int(data["soc_pct"])
            current_ma = data.get("current_ma")
            current_ma = float(current_ma) if current_ma is not None else None
        except (ValueError, KeyError, TypeError) as e:
            log.warning("pico_telemetry_parse_error", error=str(e), payload=msg.payload[:200])
            return

        new_mode = manager.update(soc_pct, current_ma)
        _publish_power_state(c, manager)

        if new_mode == PowerMode.CRITICAL and not _shutdown_requested:
            _shutdown_requested = True
            request_shutdown()
        elif new_mode != PowerMode.CRITICAL:
            _shutdown_requested = False

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    client.loop_start()

    last_heartbeat = 0.0
    last_nodes_publish = 0.0

    try:
        while _running:
            now = time.monotonic()

            if now - last_heartbeat >= settings.heartbeat_interval_seconds:
                client.publish(
                    settings.heartbeat_topic,
                    json.dumps({"ts": time.time()}),
                )
                last_heartbeat = now

            # Publish field node online count every 60 s
            if now - last_nodes_publish >= 60:
                online = sum(1 for t in _field_nodes.values() if now - t < _NODE_TIMEOUT_SECONDS)
                client.publish(
                    f"securitymesh/{settings.node_id}/nodes/online",
                    str(online),
                    retain=True,
                )
                last_nodes_publish = now

            time.sleep(1)
    finally:
        client.loop_stop()
        client.disconnect()
        log.info("gateway_power_stopped")
