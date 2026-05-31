import datetime
import time
from pathlib import Path

import structlog
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

from field_node.config import settings

log = structlog.get_logger()


class Camera:
    def __init__(self) -> None:
        self._cam = Picamera2()
        capture_config = self._cam.create_still_configuration(
            main={"size": (settings.capture_width, settings.capture_height)}
        )
        self._cam.configure(capture_config)
        if settings.camera_ir_compensation:
            # Disable AWB and apply fixed gains to compensate for missing IR-cut filter.
            # AWB cannot correct IR contamination because the sensor physically receives IR
            # in the red channel; manual gains are the only software mitigation.
            self._cam.set_controls({
                "AwbEnable": False,
                "ColourGains": (settings.camera_colour_gain_r, settings.camera_colour_gain_b),
            })
        self._cam.start()
        self._active = True
        Path(settings.capture_dir).mkdir(parents=True, exist_ok=True)
        log.info(
            "camera_ready",
            width=settings.capture_width,
            height=settings.capture_height,
            ir_compensation=settings.camera_ir_compensation,
        )

    @property
    def is_active(self) -> bool:
        return self._active

    def standby(self) -> None:
        """Stop the sensor to save ~100 mA; config is preserved for fast wakeup."""
        if self._active:
            self._cam.stop()
            self._active = False
            log.info("camera_standby")

    def wakeup(self) -> None:
        """Restart sensor and wait for stabilisation before capture."""
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

    def capture_clip(self, duration_seconds: int = 10) -> Path:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(settings.capture_dir) / f"{settings.node_id}_{timestamp}.h264"
        video_config = self._cam.create_video_configuration(
            main={"size": (settings.capture_width, settings.capture_height)}
        )
        self._cam.switch_mode(video_config)
        encoder = H264Encoder()
        output = FileOutput(str(path))
        self._cam.start_recording(encoder, output)
        time.sleep(duration_seconds)
        self._cam.stop_recording()
        self._cam.switch_mode(
            self._cam.create_still_configuration(
                main={"size": (settings.capture_width, settings.capture_height)}
            )
        )
        log.info("clip_captured", path=str(path), duration=duration_seconds)
        return path

    def close(self) -> None:
        if self._active:
            self._cam.stop()
        self._cam.close()
