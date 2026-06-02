"""Thin MQTT wrapper for the Pico watchdog (MicroPython, umqtt.simple).

Publishes battery/solar telemetry and listens for the Pi's heartbeat (so the
hardware watchdog knows the Pi is alive) plus optional operator commands. Note:
MQTT is for *telemetry and remote management only* — the power-gate safety loop
in ``main.py`` never depends on connectivity. If the broker is unreachable the
gate keeps protecting the battery from local voltage readings alone.
"""

import json
import time

import config
from umqtt.simple import MQTTClient  # type: ignore[import-not-found]  # MicroPython lib


class MQTTLink:
    def __init__(self, client_id: str, host: str, user: str, password: str) -> None:
        self._client = MQTTClient(client_id, host, user=user, password=password, keepalive=60)
        self._last_heartbeat_ms = 0
        self._connected = False

    def connect(self) -> bool:
        try:
            self._client.set_callback(self._on_message)
            self._client.connect()
            self._client.subscribe(config.HEARTBEAT_TOPIC)
            self._client.subscribe(config.COMMAND_TOPIC)
            self._last_heartbeat_ms = time.ticks_ms()  # type: ignore[attr-defined]
            self._connected = True
        except Exception:  # noqa: BLE001 — connectivity is best-effort
            self._connected = False
        return self._connected

    def _on_message(self, topic: bytes, msg: bytes) -> None:
        if topic == config.HEARTBEAT_TOPIC:
            self._last_heartbeat_ms = time.ticks_ms()  # type: ignore[attr-defined]

    def heartbeat_age_s(self) -> float:
        delta = time.ticks_diff(time.ticks_ms(), self._last_heartbeat_ms)  # type: ignore[attr-defined]
        return delta / 1000.0

    def reset_heartbeat(self) -> None:
        """Treat now as a fresh heartbeat — used after a watchdog power-cycle so
        the just-rebooted Pi isn't immediately flagged as hung again."""
        self._last_heartbeat_ms = time.ticks_ms()  # type: ignore[attr-defined]

    def poll(self) -> None:
        if self._connected:
            try:
                self._client.check_msg()
            except Exception:  # noqa: BLE001
                self._connected = False

    def publish_telemetry(self, payload: dict) -> None:
        if not self._connected:
            return
        try:
            self._client.publish(config.TELEMETRY_TOPIC, json.dumps(payload))
        except Exception:  # noqa: BLE001
            self._connected = False
