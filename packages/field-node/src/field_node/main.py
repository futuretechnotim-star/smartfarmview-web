import signal
import time

import structlog

from field_node.camera import Camera
from field_node.config import settings
from field_node.power.base import PowerMonitor, PowerReading
from field_node.power.manager import PowerManager
from field_node.telemetry import TelemetryPublisher

log = structlog.get_logger()
_running = True


def _load_power_monitor() -> PowerMonitor | None:
    if settings.power_monitor == "ina219_hat":
        try:
            from field_node.power.ina219_hat import INA219HatMonitor

            monitor = INA219HatMonitor()
            log.info("power_monitor_ready", driver="ina219_hat")
            return monitor
        except Exception as e:
            log.warning("power_monitor_unavailable", driver="ina219_hat", error=str(e))
    else:
        log.warning("power_monitor_unknown", driver=settings.power_monitor)
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

    def on_command(payload: dict[str, object]) -> None:
        cmd = payload.get("cmd")
        if cmd == "capture":
            log.info("capture_command_received")
            if not power_manager.camera_enabled:
                log.warning("capture_blocked", reason="critical_power_mode")
                return
            camera.wakeup()
            path = camera.capture_still()
            if power_manager.camera_standby_between_captures:
                camera.standby()
            telemetry.publish_snapshot(path.read_bytes())

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
                    power_manager.update(reading.soc_pct)
                    if not power_manager.camera_enabled:
                        camera.standby()

                telemetry.publish_heartbeat(
                    power=reading,
                    power_mode=power_manager.mode.value,
                )
                last_telemetry = now

            # PIR motion detection will be wired here once the sensor is added.

            time.sleep(1)
    finally:
        camera.close()
        if power is not None:
            power.close()
        telemetry.close()
        log.info("field_node_stopped")
