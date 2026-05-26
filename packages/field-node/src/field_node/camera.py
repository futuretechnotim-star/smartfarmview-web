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
        self._cam.start()
        Path(settings.capture_dir).mkdir(parents=True, exist_ok=True)
        log.info("camera_ready", width=settings.capture_width, height=settings.capture_height)

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
        self._cam.switch_mode(self._cam.create_still_configuration(
            main={"size": (settings.capture_width, settings.capture_height)}
        ))
        log.info("clip_captured", path=str(path), duration=duration_seconds)
        return path

    def close(self) -> None:
        self._cam.stop()
        self._cam.close()
