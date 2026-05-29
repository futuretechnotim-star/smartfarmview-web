import threading
import time
from collections.abc import Callable

import structlog
from gpiozero import MotionSensor

from field_node.config import settings

log = structlog.get_logger()


class PIRSensor:
    """
    HC-SR501 PIR motion sensor driver.

    Fires on_motion / on_clear callbacks from gpiozero's background thread.
    Suppresses all callbacks during the sensor's warm-up window (default 60 s).
    """

    def __init__(self, pin: int | None = None) -> None:
        self._pin = pin if pin is not None else settings.pir_gpio_pin
        self._sensor = MotionSensor(self._pin)
        self._sensor.when_motion = self._handle_motion
        self._sensor.when_no_motion = self._handle_clear

        self.on_motion: Callable[[], None] | None = None
        self.on_clear: Callable[[], None] | None = None

        self._warming_up = True
        self._last_detected_at: float | None = None

        self._warmup_timer = threading.Timer(settings.pir_warmup_seconds, self._warmup_complete)
        self._warmup_timer.daemon = True
        self._warmup_timer.start()
        log.info(
            "pir_warming_up",
            pin=self._pin,
            warmup_seconds=settings.pir_warmup_seconds,
        )

    @property
    def is_warming_up(self) -> bool:
        return self._warming_up

    @property
    def is_detected(self) -> bool:
        return bool(self._sensor.motion_detected)

    @property
    def last_detected_at(self) -> float | None:
        return self._last_detected_at

    def _warmup_complete(self) -> None:
        self._warming_up = False
        log.info("pir_ready", pin=self._pin, motion_detected=self.is_detected)

    def _handle_motion(self) -> None:
        if self._warming_up:
            return
        self._last_detected_at = time.time()
        log.info("motion_detected", pin=self._pin)
        if self.on_motion is not None:
            try:
                self.on_motion()
            except Exception as e:
                log.warning("motion_callback_error", error=str(e))

    def _handle_clear(self) -> None:
        if self._warming_up:
            return
        log.info("motion_cleared", pin=self._pin)
        if self.on_clear is not None:
            try:
                self.on_clear()
            except Exception as e:
                log.warning("clear_callback_error", error=str(e))

    def close(self) -> None:
        self._warmup_timer.cancel()
        self._sensor.close()
        log.info("pir_closed", pin=self._pin)
