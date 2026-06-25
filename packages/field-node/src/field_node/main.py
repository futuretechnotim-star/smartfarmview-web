import datetime
import json
import signal
import time
import uuid
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


def _load_camera() -> Camera | None:
    """Initialise the camera, returning None if it can't (e.g. sensor not detected).

    A camera fault must not take the node dark: telemetry, power management and
    PIR keep running, and `camera_ok=false` is reported so HA can surface the
    degraded state (mirrors the power monitor's degraded mode in docs/power-hat.md).
    """
    try:
        return Camera()
    except Exception as e:
        log.warning("camera_unavailable", error=str(e))
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


_LOG_FILENAME = "detection_log.json"


def _store_dir() -> Path | None:
    """Return the detection store directory Path, or None if not configured or unwriteable."""
    if not settings.detection_store_dir:
        return None
    p = Path(settings.detection_store_dir)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("detection_store_dir_unavailable", path=str(p), error=str(e))
        return None
    return p


def _store_detection_image(detection_id: str, jpeg_bytes: bytes) -> str | None:
    """Write hi-res JPEG to detection store.

    Returns the HA media-relative path (landplan/{node_id}/{id}.jpg) so the
    LandPlan API can proxy it via /media/local/{imageFilename} without
    reconstructing the path from metadata.  Returns None on failure.

    Convention: detection_store_dir must be {ha_media_mount}/landplan/{node_id},
    e.g. /mnt/ha-media/landplan/LandPlanMesh1.  pi-setup.sh sets this automatically.
    """
    store = _store_dir()
    if store is None:
        return None
    filename = f"{detection_id}.jpg"
    # Relative path from the HA media share root — preserves node_id case.
    ha_rel_path = f"landplan/{settings.node_id}/{filename}"
    try:
        (store / filename).write_bytes(jpeg_bytes)
        log.info("detection_image_saved", ha_rel_path=ha_rel_path)
        return ha_rel_path
    except Exception as e:
        log.warning("detection_image_save_failed", error=str(e))
        return None


def _delete_detection_image(event: dict[str, object]) -> None:
    """Delete the image file for an evicted detection event."""
    store = _store_dir()
    filename = event.get("imageFilename")
    if store is None or not filename:
        return
    try:
        (store / str(filename)).unlink(missing_ok=True)
    except Exception as e:
        log.warning("detection_image_delete_failed", filename=filename, error=str(e))


def _persist_detection_log(events: list[dict[str, object]]) -> None:
    """Write the detection log to disk so it survives a Pi restart."""
    store = _store_dir()
    if store is None:
        return
    try:
        (store / _LOG_FILENAME).write_text(json.dumps(events))
    except Exception as e:
        log.warning("detection_log_persist_failed", error=str(e))


def _load_detection_log() -> list[dict[str, object]]:
    """Load persisted detection log from disk on startup."""
    store = _store_dir()
    if store is None:
        return []
    log_file = store / _LOG_FILENAME
    if not log_file.exists():
        return []
    try:
        data = json.loads(log_file.read_text())
        if isinstance(data, list):
            log.info("detection_log_loaded", count=len(data))
            return data
    except Exception as e:
        log.warning("detection_log_load_failed", error=str(e))
    return []


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("field_node_starting", node_id=settings.node_id)

    camera = _load_camera()
    power = _load_power_monitor()
    power_manager = PowerManager()
    detector = _load_detector()
    pir = PIRSensor()
    telemetry: TelemetryPublisher  # assigned below; closures capture by name (late binding)

    # Rolling detection history — newest event at index 0.
    # Loaded from disk on startup (if detection_store_dir is set) so history
    # survives a Pi restart.
    detection_log: list[dict[str, object]] = _load_detection_log()

    def _capture() -> tuple[Path, bytes] | None:
        """Motion-triggered capture. Returns None if camera unavailable or motion capture is off."""
        if camera is None:
            return None
        if not power_manager.motion_capture_enabled:
            log.warning("capture_blocked", reason="motion_capture_disabled")
            return None
        camera.wakeup()
        path = camera.capture_still()
        if power_manager.camera_standby_between_captures:
            camera.standby()
        return path, path.read_bytes()

    def _periodic_capture() -> tuple[Path, bytes] | None:
        """Scheduled check-in capture in CRITICAL mode — bypasses the motion gate."""
        if camera is None:
            return None
        camera.wakeup()
        path = camera.capture_still()
        camera.standby()
        return path, path.read_bytes()

    def _publish_capture(
        jpeg_bytes: bytes,
        path: Path,
        *,
        no_detection_summary: str,
        suppress_if_no_detection: bool,
    ) -> None:
        """Run detection (if available), store the detection log event, and publish."""
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

        if suppress_if_no_detection and detector is not None and not detections:
            path.unlink(missing_ok=True)
            log.info("motion_suppressed", reason="no_objects_detected")
            return

        detection_id = str(uuid.uuid4())
        image_filename: str | None = _store_detection_image(detection_id, jpeg_bytes)
        detected_at = datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds")
        summary_parts = [f"{d.label} ({d.confidence:.0%})" for d in detections[:3]]
        event: dict[str, object] = {
            "id": detection_id,
            "detectedAt": detected_at,
            "summary": ", ".join(summary_parts) if summary_parts else no_detection_summary,
            "objects": [{"label": d.label, "confidence": d.confidence} for d in detections],
            "imageFilename": image_filename,
        }

        if len(detection_log) >= settings.detection_store_count:
            evicted = detection_log.pop()
            _delete_detection_image(evicted)

        detection_log.insert(0, event)
        _persist_detection_log(detection_log)

        telemetry.publish_snapshot(jpeg_bytes)
        telemetry.publish_motion_event(str(path))
        if detections:
            telemetry.publish_detection(detections, inference_ms=inference_ms)
        telemetry.publish_detection_log(detection_log)

    def on_motion() -> None:
        result = _capture()
        if result is None:
            return
        path, jpeg_bytes = result
        _publish_capture(
            jpeg_bytes, path,
            no_detection_summary="Motion detected",
            suppress_if_no_detection=True,
        )

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
    last_periodic_capture = 0.0

    try:
        while _running:
            now = time.monotonic()

            # Periodic check-in capture — fires in CRITICAL mode (daytime only).
            # Motion capture is suppressed in CRITICAL, so this is the only way
            # the node stays observable. Always runs detection and publishes the image.
            periodic_interval = power_manager.periodic_capture_interval_s
            if periodic_interval is not None and now - last_periodic_capture >= periodic_interval:
                log.info("periodic_capture_starting", interval_s=periodic_interval)
                result = _periodic_capture()
                last_periodic_capture = now
                if result is not None:
                    path, jpeg_bytes = result
                    _publish_capture(
                        jpeg_bytes, path,
                        no_detection_summary="Periodic check-in",
                        suppress_if_no_detection=False,
                    )

            if now - last_telemetry >= power_manager.telemetry_interval_seconds:
                reading: PowerReading | None = None
                if power is not None:
                    try:
                        reading = power.read()
                    except Exception as e:
                        log.warning("power_read_error", error=str(e))

                prev_mode = power_manager.mode
                if reading is not None:
                    power_manager.update(reading.soc_pct, reading.current_ma)

                # Put camera in standby on transition into CRITICAL/night — but run
                # picamera2.stop() in a daemon thread so the main loop doesn't block.
                # picamera2.stop() can take multiple seconds completing C++ teardown.
                mode_changed = power_manager.mode != prev_mode
                if mode_changed and not power_manager.motion_capture_enabled and camera is not None:
                    import threading
                    threading.Thread(target=camera.standby, daemon=True).start()

                log.info("heartbeat_publishing")
                telemetry.publish_heartbeat(
                    power=reading,
                    power_mode=power_manager.mode.value,
                    solar_status=power_manager.solar_status,
                    camera_ok=camera is not None,
                )
                log.info("heartbeat_published")
                last_telemetry = now

            time.sleep(1)
    finally:
        pir.close()
        if camera is not None:
            camera.close()
        if power is not None:
            power.close()
        telemetry.close()
        log.info("field_node_stopped")
