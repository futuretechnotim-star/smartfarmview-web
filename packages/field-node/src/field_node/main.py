import signal
import time
from pathlib import Path

import structlog

from field_node.camera import Camera
from field_node.config import settings
from field_node.motion import PIRSensor
from field_node.power.base import PowerMonitor, PowerReading
from field_node.power.manager import PowerManager
from field_node.telemetry import TelemetryPublisher

log = structlog.get_logger()
_running = True


_POWER_MONITOR_RETRY_SECONDS = 30
_POWER_MONITOR_RETRY_INTERVAL = 2


def _load_power_monitor() -> PowerMonitor | None:
    if settings.power_monitor != "ina219_hat":
        log.warning("power_monitor_unknown", driver=settings.power_monitor)
        return None

    from field_node.power.ina219_hat import INA219HatMonitor

    deadline = time.monotonic() + _POWER_MONITOR_RETRY_SECONDS
    attempt = 0
    last_error = ""
    while time.monotonic() < deadline:
        attempt += 1
        try:
            monitor = INA219HatMonitor()
            log.info("power_monitor_ready", driver="ina219_hat", attempt=attempt)
            return monitor
        except Exception as e:
            last_error = str(e)
            log.info("power_monitor_waiting", attempt=attempt, error=last_error)
            time.sleep(_POWER_MONITOR_RETRY_INTERVAL)

    log.warning(
        "power_monitor_unavailable", driver="ina219_hat", attempts=attempt, error=last_error
    )
    return None


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    log.info("shutdown_signal_received", signum=signum)
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("field_node_starting", node_id=settings.node_id)

    camera = Camera()
    power = _load_power_monitor()
    power_manager = PowerManager()
    pir = PIRSensor()
    telemetry: TelemetryPublisher  # assigned below; closures capture it by name (late binding)

    def _do_capture() -> Path | None:
        """Capture a still, publish the snapshot, return the path. Returns None if blocked."""
        if not power_manager.camera_enabled:
            log.warning("capture_blocked", reason="critical_power_mode")
            return None
        camera.wakeup()
        path = camera.capture_still()
        if power_manager.camera_standby_between_captures:
            camera.standby()
        telemetry.publish_snapshot(path.read_bytes())
        return path

    def on_motion() -> None:
        path = _do_capture()
        if path is not None:
            telemetry.publish_motion_event(str(path))

    pir.on_motion = on_motion
    pir.on_clear = lambda: telemetry.publish_motion_clear()

    def on_command(payload: dict[str, object]) -> None:
        cmd = payload.get("cmd")
        if cmd == "capture":
            log.info("capture_command_received")
            _do_capture()

    telemetry = TelemetryPublisher(on_command=on_command)
    telemetry.connect()

    last_telemetry = 0.0

    try:
        while _running:
            now = time.monotonic()

            if now - last_telemetry >= power_manager.telemetry_interval_seconds:
                reading: PowerReading | None = None
                if power is not None:
                    try:
                        reading = power.read()
                    except Exception as e:
                        log.warning("power_read_error", error=str(e))

                if reading is not None:
                    power_manager.update(reading.soc_pct, reading.current_ma)
                    if not power_manager.camera_enabled:
                        camera.standby()

                telemetry.publish_heartbeat(
                    power=reading,
                    power_mode=power_manager.mode.value,
                    solar_status=power_manager.solar_status,
                )
                last_telemetry = now

            time.sleep(1)
    finally:
        pir.close()
        camera.close()
        if power is not None:
            power.close()
        telemetry.close()
        log.info("field_node_stopped")
