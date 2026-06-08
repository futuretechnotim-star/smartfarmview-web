import datetime
import time
from pathlib import Path

import structlog
from picamera2 import Picamera2

from gateway_node.config import settings

log = structlog.get_logger()


class Camera:
    def __init__(self) -> None:
        self._cam = Picamera2()
        frame_duration_us = int(1_000_000 / settings.camera_framerate)
        capture_config = self._cam.create_still_configuration(
            main={"size": (settings.capture_width, settings.capture_height)},
            controls={"FrameDurationLimits": (frame_duration_us, frame_duration_us)},
        )
        self._cam.configure(capture_config)
        self._cam.start()
        self._active = True
        Path(settings.capture_dir).mkdir(parents=True, exist_ok=True)
        log.info(
            "camera_ready",
            width=settings.capture_width,
            height=settings.capture_height,
            framerate=settings.camera_framerate,
        )

    @property
    def is_active(self) -> bool:
        return self._active

    def standby(self) -> None:
        """Stop the sensor to save power; config is preserved for fast wakeup."""
        if self._active:
            self._cam.stop()
            self._active = False
            log.info("camera_standby")

    def wakeup(self) -> None:
        if not self._active:
            self._cam.start()
            time.sleep(2)
            self._active = True
            log.info("camera_wakeup")

    def capture_still(self) -> Path:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(settings.capture_dir) / f"{settings.node_id}_{timestamp}.jpg"
        self._cam.capture_file(str(path))
        log.info("still_captured", path=str(path))
        return path

    def close(self) -> None:
        if self._active:
            self._cam.stop()
        self._cam.close()
