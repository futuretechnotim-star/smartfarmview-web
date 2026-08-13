"""
Gateway camera streaming service (gateway-camera.service).

Runs the Camera Module 3 Wide as a continuous MJPEG stream over HTTP:
  GET /snapshot  → single JPEG still  (HA still_image_url, Landplan thumbnail)
  GET /stream    → MJPEG live stream  (browser / VLC direct access)

Also controls the Arducam B0283 pan/tilt arm (PCA9685 on I2C1, ch0=pan, ch1=tilt)
via MQTT number entities in Home Assistant:
  securitymesh/{node_id}/camera/pan/set   → target angle (0–180)
  securitymesh/{node_id}/camera/tilt/set  → target angle (0–180)
  securitymesh/{node_id}/camera/pan       → current angle (published after move)
  securitymesh/{node_id}/camera/tilt      → current angle (published after move)

Stopped by the power brain on CRITICAL power mode — add 'gateway-camera' to
GATEWAY_NODE_CRITICAL_STOP when service_control is set to 'systemd'.
"""

from __future__ import annotations

import io
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import paho.mqtt.client as mqtt
import structlog
from adafruit_servokit import ServoKit
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

from gateway_node.config import settings

log = structlog.get_logger()

_running = True

# IP of the Pi 5 host as seen from inside the hassio Docker network.
_HASSIO_BRIDGE_IP = "172.30.32.1"

# Stream resolution: half of native capture size — manageable over LTE.
_STREAM_WIDTH = settings.capture_width // 2  # 1164
_STREAM_HEIGHT = settings.capture_height // 2  # 874

# Pan/tilt servo channels on the PCA9685 (Arducam B0283).
_PAN_CHANNEL = 0
_TILT_CHANNEL = 1
_PAN_MIN, _PAN_MAX = 0, 180
_TILT_MIN, _TILT_MAX = 0, 180
_CENTER = 90


# ── Frame buffer ──────────────────────────────────────────────────────────────


class _FrameBuffer(io.BufferedIOBase):
    """Single-slot frame buffer fed by MJPEGEncoder; threads wait on condition."""

    def __init__(self) -> None:
        self.frame: bytes = b""
        self._cond = threading.Condition()

    def write(self, buf: bytes) -> int:  # type: ignore[override]
        with self._cond:
            self.frame = buf
            self._cond.notify_all()
        return len(buf)

    def latest(self, timeout: float = 2.0) -> bytes:
        with self._cond:
            self._cond.wait(timeout)
            return self.frame


# ── HTTP handler ──────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    buf: _FrameBuffer  # injected before server starts

    def do_GET(self) -> None:
        if self.path == "/snapshot":
            self._snapshot()
        elif self.path == "/stream":
            self._mjpeg()
        else:
            self.send_response(404)
            self.end_headers()

    def _snapshot(self) -> None:
        frame = self.__class__.buf.latest()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(frame)

    def _mjpeg(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()
        try:
            while True:
                frame = self.__class__.buf.latest()
                self.wfile.write(
                    b"--FRAME\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode()
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
        except Exception:
            pass  # client disconnected — normal exit

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress per-request noise in journald


# ── Pan/tilt controller ───────────────────────────────────────────────────────

# Sweep pace: degrees moved per tick and the delay between ticks.
_SWEEP_STEP_DEG = 2.0
_SWEEP_DELAY_S = 0.01


class _AxisMover:
    """Runs one servo channel toward whatever target was set most recently.

    A single background thread owns the channel. `set_target` just records the
    new target and wakes the thread — it never blocks and never queues stale
    intermediate targets, so rapid successive commands (e.g. dragging a slider)
    collapse into "go to the latest position" instead of visibly replaying
    every position passed through along the way.
    """

    def __init__(
        self,
        kit: ServoKit,
        channel: int,
        start: float,
        min_angle: float,
        max_angle: float,
        on_reached: Any,
    ) -> None:
        self._kit = kit
        self._channel = channel
        self._min = min_angle
        self._max = max_angle
        self._on_reached = on_reached
        self._current = float(start)
        self._target = float(start)
        self._cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def position(self) -> float:
        with self._cond:
            return self._current

    def set_target(self, target: float) -> float:
        target = max(self._min, min(self._max, target))
        with self._cond:
            self._target = target
            self._cond.notify_all()
        return target

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._current == self._target:
                    self._cond.wait()
                target = self._target
            if self._current < target:
                self._current = min(target, self._current + _SWEEP_STEP_DEG)
            else:
                self._current = max(target, self._current - _SWEEP_STEP_DEG)
            self._kit.servo[self._channel].angle = self._current
            if self._current == target:
                self._on_reached(self._current)
            time.sleep(_SWEEP_DELAY_S)


class _PanTilt:
    """Owns the pan and tilt axes; each moves independently and concurrently."""

    def __init__(self, on_pan_reached: Any, on_tilt_reached: Any) -> None:
        self._kit = ServoKit(channels=16)
        # Start centred
        self._kit.servo[_PAN_CHANNEL].angle = _CENTER
        self._kit.servo[_TILT_CHANNEL].angle = _CENTER
        self._pan_axis = _AxisMover(
            self._kit, _PAN_CHANNEL, _CENTER, _PAN_MIN, _PAN_MAX, on_pan_reached
        )
        self._tilt_axis = _AxisMover(
            self._kit, _TILT_CHANNEL, _CENTER, _TILT_MIN, _TILT_MAX, on_tilt_reached
        )
        log.info("pan_tilt_initialised", pan=_CENTER, tilt=_CENTER)

    @property
    def pan(self) -> float:
        return self._pan_axis.position

    @property
    def tilt(self) -> float:
        return self._tilt_axis.position

    def set_pan(self, target: float) -> float:
        return self._pan_axis.set_target(target)

    def set_tilt(self, target: float) -> float:
        return self._tilt_axis.set_target(target)


# ── MQTT discovery ────────────────────────────────────────────────────────────


def _publish_discovery(client: mqtt.Client, node_id: str, pan: float, tilt: float) -> None:
    prefix = settings.mqtt_discovery_prefix
    pan_topic = f"securitymesh/{node_id}/camera/pan"
    tilt_topic = f"securitymesh/{node_id}/camera/tilt"
    status_topic = f"securitymesh/{node_id}/camera/status"

    # Separate sub-device for the camera + pan/tilt hardware, linked to the
    # gateway device (created by gateway-power) via `via_device` below.
    camera_device = {
        "identifiers": [f"{node_id}_camera"],
        "name": "Gateway Camera",
        "model": "Camera Module 3 Wide + B0283 Pan/Tilt",
        "manufacturer": "Raspberry Pi / Arducam",
        "via_device": node_id,
    }

    entities: list[tuple[str, str, dict[str, object]]] = [
        # Camera online status
        (
            "binary_sensor",
            f"{node_id}_camera_online",
            {
                "name": "Gateway Camera Online",
                "unique_id": f"{node_id}_camera_online",
                "device_class": "connectivity",
                "state_topic": status_topic,
                "payload_on": "online",
                "payload_off": "offline",
                "device": camera_device,
            },
        ),
        # Pan control
        (
            "number",
            f"{node_id}_pan",
            {
                "name": "Camera Pan",
                "unique_id": f"{node_id}_pan",
                "command_topic": f"{pan_topic}/set",
                "state_topic": pan_topic,
                "min": _PAN_MIN,
                "max": _PAN_MAX,
                "step": 1,
                "unit_of_measurement": "°",
                "icon": "mdi:pan-horizontal",
                "device": camera_device,
            },
        ),
        # Tilt control
        (
            "number",
            f"{node_id}_tilt",
            {
                "name": "Camera Tilt",
                "unique_id": f"{node_id}_tilt",
                "command_topic": f"{tilt_topic}/set",
                "state_topic": tilt_topic,
                "min": _TILT_MIN,
                "max": _TILT_MAX,
                "step": 1,
                "unit_of_measurement": "°",
                "icon": "mdi:pan-vertical",
                "device": camera_device,
            },
        ),
    ]

    for component, object_id, config in entities:
        topic = f"{prefix}/{component}/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)

    # Publish initial servo positions and online status
    client.publish(pan_topic, str(int(pan)), retain=True)
    client.publish(tilt_topic, str(int(tilt)), retain=True)
    client.publish(status_topic, "online", retain=True)
    log.info("camera_discovery_published", pan=pan, tilt=tilt)


# ── Service entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    global _running

    def _stop(signum: int, frame: object) -> None:
        global _running
        _running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    node_id = settings.node_id
    pan_cmd_topic = f"securitymesh/{node_id}/camera/pan/set"
    tilt_cmd_topic = f"securitymesh/{node_id}/camera/tilt/set"
    pan_state_topic = f"securitymesh/{node_id}/camera/pan"
    tilt_state_topic = f"securitymesh/{node_id}/camera/tilt"
    status_topic = f"securitymesh/{node_id}/camera/status"

    # ── MQTT ─────────────────────────────────────────────────────────────────
    client: mqtt.Client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
        client_id=f"{node_id}-camera",
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.will_set(status_topic, "offline", retain=True)

    # ── Pan/tilt ─────────────────────────────────────────────────────────────
    def _on_pan_reached(angle: float) -> None:
        client.publish(pan_state_topic, str(int(angle)), retain=True)
        log.info("pan_moved", angle=angle)

    def _on_tilt_reached(angle: float) -> None:
        client.publish(tilt_state_topic, str(int(angle)), retain=True)
        log.info("tilt_moved", angle=angle)

    pantilt = _PanTilt(_on_pan_reached, _on_tilt_reached)

    def on_connect(c: mqtt.Client, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
        c.subscribe(pan_cmd_topic)
        c.subscribe(tilt_cmd_topic)
        _publish_discovery(c, node_id, pantilt.pan, pantilt.tilt)
        log.info("mqtt_connected")

    def on_message(c: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            target = float(msg.payload.decode().strip())
        except ValueError:
            log.warning("pan_tilt_invalid_value", topic=msg.topic, payload=msg.payload[:50])
            return

        if msg.topic == pan_cmd_topic:
            pantilt.set_pan(target)
        elif msg.topic == tilt_cmd_topic:
            pantilt.set_tilt(target)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    client.loop_start()

    # ── HTTP server ───────────────────────────────────────────────────────────
    buf = _FrameBuffer()
    _Handler.buf = buf
    server = ThreadingHTTPServer(("0.0.0.0", settings.camera_stream_port), _Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    log.info("camera_http_started", port=settings.camera_stream_port)

    # ── Camera ────────────────────────────────────────────────────────────────
    picam = Picamera2()
    video_config = picam.create_video_configuration(
        main={"size": (_STREAM_WIDTH, _STREAM_HEIGHT)},
    )
    picam.configure(video_config)
    encoder = MJPEGEncoder(10_000_000)
    picam.start_recording(encoder, FileOutput(buf))
    log.info("camera_stream_started", width=_STREAM_WIDTH, height=_STREAM_HEIGHT)

    try:
        while _running:
            time.sleep(1)
    finally:
        client.publish(status_topic, "offline", retain=True)
        time.sleep(0.3)
        picam.stop_recording()
        server.shutdown()
        client.loop_stop()
        client.disconnect()
        log.info("camera_stream_stopped")
