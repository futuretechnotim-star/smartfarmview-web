import signal
import time

import structlog

from field_node.camera import Camera
from field_node.config import settings
from field_node.telemetry import TelemetryPublisher

log = structlog.get_logger()
_running = True


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    log.info("shutdown_signal_received", signum=signum)
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("field_node_starting", node_id=settings.node_id)

    camera = Camera()
    telemetry = TelemetryPublisher()
    telemetry.connect()

    last_telemetry = 0.0

    try:
        while _running:
            now = time.monotonic()

            if now - last_telemetry >= settings.telemetry_interval_seconds:
                telemetry.publish_heartbeat()
                last_telemetry = now

            # PIR motion detection will be wired here once the sensor is added.
            # For now the loop only publishes periodic telemetry.

            time.sleep(1)
    finally:
        camera.close()
        telemetry.close()
        log.info("field_node_stopped")
