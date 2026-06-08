"""Gateway power-brain service entrypoint.

Connects to the local Mosquitto broker, consumes battery/solar telemetry
published by the Pico 2 W watchdog, drives the gateway power state machine, and:

  * publishes the current power mode + solar projection back to MQTT (for Home
    Assistant dashboards/automations), and
  * publishes a periodic heartbeat the Pico's hardware watchdog watches — if it
    stops, the Pico power-cycles the Pi.
  * publishes camera snapshots (on a timer and on demand via cmd topic) and
    registers the camera entity with Home Assistant MQTT discovery.

The Pico remains the autonomous safety backstop; this service only does the
*graceful* degradation tier.
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


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    log.info("shutdown_signal_received", signum=signum)
    _running = False


def _build_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{settings.node_id}-power")  # type: ignore[attr-defined]
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    return client


def _publish_state(client: mqtt.Client, manager: GatewayPowerManager) -> None:
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


def _publish_discovery(client: mqtt.Client) -> None:
    node = settings.node_id
    prefix = settings.mqtt_discovery_prefix
    snapshot_topic = f"securitymesh/{node}/snapshot"
    cmd_topic = f"securitymesh/{node}/cmd"
    device = {
        "identifiers": [node],
        "name": node,
        "model": "SecurityMesh Gateway Node",
        "manufacturer": "SmartFarmView",
    }

    entities: list[tuple[str, str, dict[str, object]]] = [
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
        topic = f"{prefix}/{component}/{node}/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)
        log.info("discovery_published", component=component, object_id=object_id)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("gateway_power_starting", node_id=settings.node_id)
    manager = GatewayPowerManager()
    client = _build_client()
    _shutdown_requested = False
    _capture_requested = False

    # Camera is hardware-optional — absent on dev machines without picamera2.
    camera = None
    try:
        from gateway_node.camera import Camera

        camera = Camera()
        log.info("camera_initialised")
    except Exception as e:
        log.warning("camera_unavailable", error=str(e))

    def on_connect(c: mqtt.Client, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
        log.info("mqtt_connected", host=settings.mqtt_host, port=settings.mqtt_port)
        c.subscribe(settings.pico_telemetry_topic)
        c.subscribe(f"securitymesh/{settings.node_id}/cmd")
        if camera is not None:
            _publish_discovery(c)

    def on_message(c: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        nonlocal _shutdown_requested, _capture_requested

        if msg.topic == f"securitymesh/{settings.node_id}/cmd":
            try:
                data = json.loads(msg.payload.decode())
                if data.get("cmd") == "capture":
                    _capture_requested = True
            except Exception as e:
                log.warning("cmd_parse_error", error=str(e))
            return

        # Pico telemetry
        try:
            data = json.loads(msg.payload.decode())
            soc_pct = int(data["soc_pct"])
            current_ma = data.get("current_ma")
            current_ma = float(current_ma) if current_ma is not None else None
        except (ValueError, KeyError, TypeError) as e:
            log.warning("pico_telemetry_parse_error", error=str(e), payload=msg.payload[:200])
            return
        new_mode = manager.update(soc_pct, current_ma)
        _publish_state(c, manager)

        # Gate camera power alongside mode transitions.
        if camera is not None:
            if manager.camera_enabled and not camera.is_active:
                camera.wakeup()
            elif not manager.camera_enabled and camera.is_active:
                camera.standby()

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
    last_snapshot = 0.0
    snapshot_interval = settings.camera_snapshot_interval_seconds

    try:
        while _running:
            now = time.monotonic()

            if now - last_heartbeat >= settings.heartbeat_interval_seconds:
                client.publish(settings.heartbeat_topic, json.dumps({"ts": time.time()}))
                last_heartbeat = now

            if camera is not None and manager.camera_enabled:
                periodic_due = snapshot_interval > 0 and (now - last_snapshot >= snapshot_interval)
                if periodic_due or _capture_requested:
                    _capture_requested = False
                    try:
                        path = camera.capture_still()
                        jpeg_bytes = path.read_bytes()
                        client.publish(
                            f"securitymesh/{settings.node_id}/snapshot",
                            jpeg_bytes,
                            qos=1,
                            retain=True,
                        )
                        log.info("snapshot_published", size_kb=round(len(jpeg_bytes) / 1024, 1))
                        last_snapshot = now
                    except Exception as e:
                        log.warning("snapshot_error", error=str(e))

            time.sleep(1)
    finally:
        if camera is not None:
            camera.close()
        client.loop_stop()
        client.disconnect()
        log.info("gateway_power_stopped")
