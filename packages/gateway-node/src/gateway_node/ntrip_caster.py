"""Local NTRIP caster serving the gateway's RTCM3 corrections on the
field-mesh WiFi, so the survey stick's Pi Zero can pull them directly instead
of routing through NTRIP caster → internet → phone → BLE (the survey stick's
own designed but unbuilt correction path, see docs/gpsrtk.md). Reusing NTRIP
for this local hop too means the survey stick only ever needs one client
implementation.

Runs inside gateway-sensors.service (not a separate service) — gps_driver.py
owns the I2C bus, so RTCM3 bytes are fed in directly from the same polling
loop that drains them (sensors.py calls NtripCaster.feed() each tick while
BASE_ACTIVE).

Mirrors camera_server.py's ThreadingHTTPServer + streaming-handler shape:
one producer (feed()) fans out to N concurrent stream consumers. Unlike the
camera's single-slot "latest frame wins" buffer, this uses a queue per
client — RTCM3 is a differential byte stream (dropping bytes mid-message
corrupts every connected receiver's decoder), whereas a dropped JPEG frame
just self-heals on the next one.

  GET /              → NTRIP sourcetable (one STR entry, this mountpoint)
  GET /<mountpoint>  → ICY 200 OK, then raw RTCM3 bytes streamed indefinitely

No auth in this phase — the field-mesh WiFi is already access-controlled.
Required before any future LTE/phone-relay (public-facing) path is built.
"""

from __future__ import annotations

import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import structlog

log = structlog.get_logger()

_RTCM_MESSAGE_SET = "1005(1),1077(1),1230(5)"  # ARP + GPS MSM7 + GLONASS biases


class _RtcmBroadcaster:
    """Fan-out point: one write() producer, N subscribed stream consumers."""

    def __init__(self) -> None:
        self._clients: set[queue.SimpleQueue[bytes]] = set()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            for q in self._clients:
                q.put(data)

    def subscribe(self) -> queue.SimpleQueue[bytes]:
        q: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        with self._lock:
            self._clients.add(q)
        return q

    def unsubscribe(self, q: queue.SimpleQueue[bytes]) -> None:
        with self._lock:
            self._clients.discard(q)


class _Handler(BaseHTTPRequestHandler):
    broadcaster: _RtcmBroadcaster  # injected before server starts
    mountpoint: str  # injected before server starts

    def do_GET(self) -> None:
        path = self.path.lstrip("/")
        if path == "":
            self._sourcetable()
        elif path == self.__class__.mountpoint:
            self._stream()
        else:
            self.send_response(404)
            self.end_headers()

    def _sourcetable(self) -> None:
        # NTRIP v1 sourcetable format — enough for a client to discover the
        # one mountpoint by browsing rather than being told it out of band.
        entry = (
            f"STR;{self.__class__.mountpoint};SmartFarmView Gateway RTK Base;"
            f"RTCM 3.3;{_RTCM_MESSAGE_SET};2;GPS;SmartFarmView;USA;0.0;0.0;1;0;"
            "sNTRIP;none;N;N;0;\r\n"
        ).encode()
        body = entry + b"ENDSOURCETABLE\r\n"
        header = (
            "SOURCETABLE 200 OK\r\n"
            "Server: SmartFarmView-NTRIP/1.0\r\n"
            "Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode()
        self.wfile.write(header + body)

    def _stream(self) -> None:
        # NTRIP casters reply "ICY 200 OK" (not a standard HTTP status line)
        # before streaming raw bytes — written directly rather than via
        # send_response(), which would emit an HTTP/1.1 line instead.
        self.wfile.write(b"ICY 200 OK\r\n\r\n")
        q = self.__class__.broadcaster.subscribe()
        try:
            while True:
                self.wfile.write(q.get())
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected — normal exit
        finally:
            self.__class__.broadcaster.unsubscribe(q)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress per-request noise in journald


class NtripCaster:
    """Owns the HTTP server thread and the fan-out buffer. feed() is called
    from the sensors polling loop with each drain_rtcm3() chunk."""

    def __init__(self, port: int, mountpoint: str) -> None:
        self._broadcaster = _RtcmBroadcaster()
        handler = type(
            "_BoundNtripHandler",
            (_Handler,),
            {"broadcaster": self._broadcaster, "mountpoint": mountpoint},
        )
        self._server = ThreadingHTTPServer(("0.0.0.0", port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._port = port
        self._mountpoint = mountpoint

    def start(self) -> None:
        self._thread.start()
        log.info("ntrip_caster_started", port=self._port, mountpoint=self._mountpoint)

    def feed(self, data: bytes) -> None:
        self._broadcaster.write(data)

    def stop(self) -> None:
        self._server.shutdown()
        log.info("ntrip_caster_stopped")
