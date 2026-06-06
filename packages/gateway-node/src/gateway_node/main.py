"""Gateway power-brain service entrypoint.

Connects to the local Mosquitto broker, consumes battery/solar telemetry
published by the Pico 2 W watchdog, drives the gateway power state machine, and:

  * publishes the current power mode + solar projection back to MQTT (for Home
    Assistant dashboards/automations), and
  * publishes a periodic heartbeat the Pico's hardware watchdog watches — if it
    stops, the Pico power-cycles the Pi.

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


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("gateway_power_starting", node_id=settings.node_id)
    manager = GatewayPowerManager()
    client = _build_client()
    _shutdown_requested = False

    def on_connect(c: mqtt.Client, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
        log.info("mqtt_connected", host=settings.mqtt_host, port=settings.mqtt_port)
        c.subscribe(settings.pico_telemetry_topic)

    def on_message(c: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        nonlocal _shutdown_requested
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
    try:
        while _running:
            now = time.monotonic()
            if now - last_heartbeat >= settings.heartbeat_interval_seconds:
                client.publish(settings.heartbeat_topic, json.dumps({"ts": time.time()}))
                last_heartbeat = now
            time.sleep(1)
    finally:
        client.loop_stop()
        client.disconnect()
        log.info("gateway_power_stopped")
