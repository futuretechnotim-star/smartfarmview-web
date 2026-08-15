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
        self._pending_ota_base_url: str | None = None
        self._pending_test_bare_reset = False
        self._pending_reboot_gateway = False

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
        elif topic == config.COMMAND_TOPIC:
            # Just record it here — applying an OTA update does blocking HTTP
            # downloads, which has no business running inside the MQTT
            # client's own message-pump callback. main.py applies it from the
            # ordinary loop instead, via pop_pending_ota().
            try:
                data = json.loads(msg)
                cmd = data.get("cmd")
                if cmd == "ota" and data.get("base_url"):
                    self._pending_ota_base_url = data["base_url"]
                elif cmd == "test_bare_reset":
                    self._pending_test_bare_reset = True
                elif cmd == "reboot_gateway":
                    self._pending_reboot_gateway = True
            except Exception:  # noqa: BLE001 — malformed command, ignore
                pass

    def pop_pending_ota(self) -> "str | None":
        """Return and clear a pending OTA base_url, if a command arrived."""
        base_url = self._pending_ota_base_url
        self._pending_ota_base_url = None
        return base_url

    def pop_pending_test_bare_reset(self) -> bool:
        """Return and clear a pending test_bare_reset command — see main.py's
        handling for what this diagnostic actually checks."""
        pending = self._pending_test_bare_reset
        self._pending_test_bare_reset = False
        return pending

    def pop_pending_reboot_gateway(self) -> bool:
        """Return and clear a pending reboot_gateway command — an operator-
        triggered graceful halt + PSU power cycle, for remote recovery when
        SSH/HA aren't responding. See main.py's handling."""
        pending = self._pending_reboot_gateway
        self._pending_reboot_gateway = False
        return pending

    def heartbeat_age_s(self) -> float:
        delta = time.ticks_diff(time.ticks_ms(), self._last_heartbeat_ms)  # type: ignore[attr-defined]
        return delta / 1000.0

    def reset_heartbeat(self) -> None:
        """Treat now as a fresh heartbeat — used after a watchdog power-cycle so
        the just-rebooted Pi isn't immediately flagged as hung again."""
        self._last_heartbeat_ms = time.ticks_ms()  # type: ignore[attr-defined]

    def is_connected(self) -> bool:
        return self._connected

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

    def publish(self, topic: bytes, payload: dict, retain: bool = False) -> None:
        """Publish an arbitrary JSON payload to an arbitrary topic — e.g. a
        command to the gateway's own command topic, or the retained log tail."""
        if not self._connected:
            return
        try:
            self._client.publish(topic, json.dumps(payload), retain=retain)
        except Exception:  # noqa: BLE001
            self._connected = False
