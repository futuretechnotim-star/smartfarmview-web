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

    False-positive filtering:
      - pir_queue_len: gpiozero samples the pin at 10 Hz and requires a majority
        of queue_len samples to be HIGH before raising motion_detected. This
        smooths out sub-millisecond electrical glitches before they reach this code.
      - pir_min_duration_seconds: the pin must remain HIGH for this long before
        on_motion is fired. If it drops before the timer expires the event is
        discarded (catches 1-second sensor-mode pulses).
      - pir_cooldown_seconds: minimum gap between consecutive on_motion callbacks.
    """

    def __init__(self, pin: int | None = None) -> None:
        self._pin = pin if pin is not None else settings.pir_gpio_pin
        self._sensor = MotionSensor(self._pin, queue_len=settings.pir_queue_len)
        self._sensor.when_motion = self._handle_motion
        self._sensor.when_no_motion = self._handle_clear

        self.on_motion: Callable[[], None] | None = None
        self.on_clear: Callable[[], None] | None = None

        self._warming_up = True
        self._last_detected_at: float | None = None
        self._last_fired_at: float = 0.0
        self._motion_active = False  # True after on_motion fired, until on_clear fires
        self._pending_timer: threading.Timer | None = None
        self._lock = threading.Lock()

        self._warmup_timer = threading.Timer(settings.pir_warmup_seconds, self._warmup_complete)
        self._warmup_timer.daemon = True
        self._warmup_timer.start()
        log.info(
            "pir_warming_up",
            pin=self._pin,
            warmup_seconds=settings.pir_warmup_seconds,
            queue_len=settings.pir_queue_len,
            min_duration=settings.pir_min_duration_seconds,
            cooldown=settings.pir_cooldown_seconds,
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
        with self._lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
            timer = threading.Timer(settings.pir_min_duration_seconds, self._confirm_motion)
            timer.daemon = True
            self._pending_timer = timer
        timer.start()

    def _confirm_motion(self) -> None:
        """Called after min_duration_seconds — validates and fires on_motion."""
        with self._lock:
            self._pending_timer = None
            if not self._sensor.motion_detected:
                log.debug("motion_discarded_pin_cleared", pin=self._pin)
                return
            now = time.time()
            if now - self._last_fired_at < settings.pir_cooldown_seconds:
                log.debug(
                    "motion_discarded_cooldown",
                    pin=self._pin,
                    remaining=round(settings.pir_cooldown_seconds - (now - self._last_fired_at), 1),
                )
                return
            self._last_detected_at = now
            self._last_fired_at = now
            self._motion_active = True

        log.info("motion_detected", pin=self._pin)
        if self.on_motion is not None:
            try:
                self.on_motion()
            except Exception as e:
                log.warning("motion_callback_error", error=str(e))

    def _handle_clear(self) -> None:
        if self._warming_up:
            return
        with self._lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None
            if not self._motion_active:
                return
            self._motion_active = False

        log.info("motion_cleared", pin=self._pin)
        if self.on_clear is not None:
            try:
                self.on_clear()
            except Exception as e:
                log.warning("clear_callback_error", error=str(e))

    def close(self) -> None:
        with self._lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None
        self._warmup_timer.cancel()
        self._sensor.close()
        log.info("pir_closed", pin=self._pin)
