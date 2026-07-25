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


class _PanTilt:
    """Thread-safe servo controller. Sweeps smoothly to target angles."""

    def __init__(self) -> None:
        self._kit = ServoKit(channels=16)
        self._lock = threading.Lock()
        self._pan = float(_CENTER)
        self._tilt = float(_CENTER)
        # Start centred
        self._kit.servo[_PAN_CHANNEL].angle = _CENTER
        self._kit.servo[_TILT_CHANNEL].angle = _CENTER
        log.info("pan_tilt_initialised", pan=_CENTER, tilt=_CENTER)

    @property
    def pan(self) -> float:
        return self._pan

    @property
    def tilt(self) -> float:
        return self._tilt

    def move_pan(self, target: float) -> float:
        target = max(_PAN_MIN, min(_PAN_MAX, target))
        with self._lock:
            self._sweep(_PAN_CHANNEL, self._pan, target)
            self._pan = target
        return target

    def move_tilt(self, target: float) -> float:
        target = max(_TILT_MIN, min(_TILT_MAX, target))
        with self._lock:
            self._sweep(_TILT_CHANNEL, self._tilt, target)
            self._tilt = target
        return target

    def _sweep(
        self, channel: int, start: float, end: float, steps: int = 30, delay: float = 0.02
    ) -> None:
        for i in range(steps + 1):
            self._kit.servo[channel].angle = start + (end - start) * i / steps
            time.sleep(delay)


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

    # ── Pan/tilt ─────────────────────────────────────────────────────────────
    pantilt = _PanTilt()

    # ── MQTT ─────────────────────────────────────────────────────────────────
    client: mqtt.Client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
        client_id=f"{node_id}-camera",
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.will_set(status_topic, "offline", retain=True)

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

            def _do_pan() -> None:
                new_pos = pantilt.move_pan(target)
                c.publish(pan_state_topic, str(int(new_pos)), retain=True)
                log.info("pan_moved", angle=new_pos)

            threading.Thread(target=_do_pan, daemon=True).start()

        elif msg.topic == tilt_cmd_topic:

            def _do_tilt() -> None:
                new_pos = pantilt.move_tilt(target)
                c.publish(tilt_state_topic, str(int(new_pos)), retain=True)
                log.info("tilt_moved", angle=new_pos)

            threading.Thread(target=_do_tilt, daemon=True).start()

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
