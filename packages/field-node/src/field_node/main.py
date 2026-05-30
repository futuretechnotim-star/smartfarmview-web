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


def _load_detector() -> "ObjectDetector | None":
    model_path = Path(settings.detector_model_path)
    labels_path = Path(settings.detector_labels_path)
    if not model_path.exists() or not labels_path.exists():
        log.warning(
            "detector_model_not_found",
            model=str(model_path),
            labels=str(labels_path),
        )
        return None
    try:
        from field_node.detector import ObjectDetector

        return ObjectDetector()
    except Exception as e:
        log.warning("detector_unavailable", error=str(e))
        return None


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    log.info("shutdown_signal_received", signum=signum)
    _running = False


# Optional import — only available when ai-edge-litert/tflite-runtime is installed on the Pi.
# Guarded so CI (without those packages) can still import this module cleanly.
try:
    from field_node.detector import Detection, ObjectDetector
except ImportError:
    Detection = None  # type: ignore[assignment,misc]
    ObjectDetector = None  # type: ignore[assignment,misc]


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("field_node_starting", node_id=settings.node_id)

    camera = Camera()
    power = _load_power_monitor()
    power_manager = PowerManager()
    detector = _load_detector()
    pir = PIRSensor()
    telemetry: TelemetryPublisher  # assigned below; closures capture by name (late binding)

    def _capture() -> tuple[Path, bytes] | None:
        """Capture a still and return (path, jpeg_bytes). Returns None if power mode blocks it."""
        if not power_manager.camera_enabled:
            log.warning("capture_blocked", reason="critical_power_mode")
            return None
        camera.wakeup()
        path = camera.capture_still()
        if power_manager.camera_standby_between_captures:
            camera.standby()
        return path, path.read_bytes()

    def on_motion() -> None:
        result = _capture()
        if result is None:
            return
        path, jpeg_bytes = result

        # Run TFLite object detection if the model is available.
        # Suppresses events where only background motion (wind, light) triggered the PIR.
        detections: list[Detection] = []
        inference_ms = 0
        if detector is not None:
            try:
                detections, inference_ms = detector.detect(jpeg_bytes)
                log.info(
                    "detection_complete",
                    objects=[d.label for d in detections],
                    inference_ms=inference_ms,
                )
            except Exception as e:
                log.warning("detection_error", error=str(e))

        if detector is not None and not detections:
            path.unlink(missing_ok=True)
            log.info("motion_suppressed", reason="no_objects_detected")
            return

        telemetry.publish_snapshot(jpeg_bytes)
        telemetry.publish_motion_event(str(path))
        if detections:
            telemetry.publish_detection(detections, inference_ms=inference_ms)

    pir.on_motion = on_motion
    pir.on_clear = lambda: telemetry.publish_motion_clear()

    def on_command(payload: dict[str, object]) -> None:
        cmd = payload.get("cmd")
        if cmd == "capture":
            log.info("capture_command_received")
            result = _capture()
            if result is not None:
                path, jpeg_bytes = result
                # Manual captures always publish — no detection filter
                telemetry.publish_snapshot(jpeg_bytes)

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
