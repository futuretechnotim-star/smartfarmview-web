import time
from unittest.mock import MagicMock, patch

import pytest

from field_node.config import settings
from field_node.motion import PIRSensor


@pytest.fixture
def mock_hw():
    """Patch the gpiozero MotionSensor so no GPIO hardware is needed."""
    with patch("field_node.motion.MotionSensor") as mock_cls:
        instance = mock_cls.return_value
        instance.motion_detected = False
        yield instance


@pytest.fixture
def pir(mock_hw: MagicMock) -> PIRSensor:
    sensor = PIRSensor(pin=26)
    yield sensor
    sensor.close()


@pytest.fixture
def ready_pir(pir: PIRSensor) -> PIRSensor:
    """PIR sensor past its warm-up phase."""
    pir._warmup_complete()
    return pir


class TestInit:
    def test_starts_warming_up(self, pir: PIRSensor) -> None:
        assert pir.is_warming_up is True

    def test_no_callbacks_set(self, pir: PIRSensor) -> None:
        assert pir.on_motion is None
        assert pir.on_clear is None

    def test_last_detected_at_is_none(self, pir: PIRSensor) -> None:
        assert pir.last_detected_at is None

    def test_uses_configured_pin(self) -> None:
        with patch("field_node.motion.MotionSensor") as mock_cls:
            mock_cls.return_value.motion_detected = False
            pir = PIRSensor(pin=17)
            pir.close()
            mock_cls.assert_called_once_with(17, queue_len=settings.pir_queue_len)

    def test_uses_settings_pin_when_none_given(self) -> None:
        with patch("field_node.motion.MotionSensor") as mock_cls:
            mock_cls.return_value.motion_detected = False
            pir = PIRSensor()
            pir.close()
            mock_cls.assert_called_once_with(
                settings.pir_gpio_pin, queue_len=settings.pir_queue_len
            )


class TestWarmup:
    def test_warmup_complete_clears_flag(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        assert pir.is_warming_up is False

    def test_motion_suppressed_during_warmup(self, pir: PIRSensor) -> None:
        cb = MagicMock()
        pir.on_motion = cb
        pir._handle_motion()
        cb.assert_not_called()

    def test_clear_suppressed_during_warmup(self, pir: PIRSensor) -> None:
        cb = MagicMock()
        pir.on_clear = cb
        pir._handle_clear()
        cb.assert_not_called()

    def test_last_detected_not_set_during_warmup(self, pir: PIRSensor) -> None:
        pir._handle_motion()
        assert pir.last_detected_at is None


class TestMinDurationFilter:
    def test_handle_motion_does_not_immediately_fire_callback(self, ready_pir: PIRSensor) -> None:
        cb = MagicMock()
        ready_pir.on_motion = cb
        ready_pir._handle_motion()
        cb.assert_not_called()

    def test_confirm_motion_fires_callback_when_pin_still_high(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        cb = MagicMock()
        ready_pir.on_motion = cb
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()
        cb.assert_called_once()

    def test_confirm_motion_discards_when_pin_cleared(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        cb = MagicMock()
        ready_pir.on_motion = cb
        mock_hw.motion_detected = False
        ready_pir._confirm_motion()
        cb.assert_not_called()

    def test_last_detected_at_set_on_confirmed_motion(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()
        assert ready_pir.last_detected_at is not None

    def test_last_detected_at_not_set_when_pin_cleared(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        mock_hw.motion_detected = False
        ready_pir._confirm_motion()
        assert ready_pir.last_detected_at is None

    def test_handle_clear_before_timer_cancels_without_callback(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        """Pin goes LOW before min_duration elapses — no callback should fire."""
        motion_cb = MagicMock()
        clear_cb = MagicMock()
        ready_pir.on_motion = motion_cb
        ready_pir.on_clear = clear_cb
        ready_pir._handle_motion()
        ready_pir._handle_clear()
        motion_cb.assert_not_called()
        clear_cb.assert_not_called()


class TestCooldown:
    def test_confirm_motion_respects_cooldown(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        cb = MagicMock()
        ready_pir.on_motion = cb
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()
        assert cb.call_count == 1
        # Second call within cooldown window should be suppressed
        ready_pir._confirm_motion()
        assert cb.call_count == 1

    def test_confirm_motion_fires_after_cooldown_elapsed(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        cb = MagicMock()
        ready_pir.on_motion = cb
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()
        # Manually expire the cooldown
        ready_pir._last_fired_at = time.time() - settings.pir_cooldown_seconds - 1
        ready_pir._motion_active = False
        ready_pir._confirm_motion()
        assert cb.call_count == 2


class TestClearCallback:
    def test_on_clear_fires_after_confirmed_motion(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        clear_cb = MagicMock()
        ready_pir.on_clear = clear_cb
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()
        ready_pir._handle_clear()
        clear_cb.assert_called_once()

    def test_on_clear_does_not_fire_without_prior_motion(self, ready_pir: PIRSensor) -> None:
        cb = MagicMock()
        ready_pir.on_clear = cb
        ready_pir._handle_clear()
        cb.assert_not_called()

    def test_is_detected_reflects_hardware_state(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        mock_hw.motion_detected = True
        assert ready_pir.is_detected is True
        mock_hw.motion_detected = False
        assert ready_pir.is_detected is False

    def test_no_callback_set_does_not_raise(self, ready_pir: PIRSensor, mock_hw: MagicMock) -> None:
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()
        ready_pir._handle_clear()

    def test_callback_exception_does_not_propagate(
        self, ready_pir: PIRSensor, mock_hw: MagicMock
    ) -> None:
        ready_pir.on_motion = MagicMock(side_effect=RuntimeError("boom"))
        mock_hw.motion_detected = True
        ready_pir._confirm_motion()  # should not raise


class TestClose:
    def test_close_releases_hardware(self, mock_hw: MagicMock) -> None:
        pir = PIRSensor(pin=26)
        pir.close()
        mock_hw.close.assert_called_once()

    def test_close_cancels_warmup_timer(self, mock_hw: MagicMock) -> None:
        pir = PIRSensor(pin=26)
        with patch.object(pir._warmup_timer, "cancel") as mock_cancel:
            pir.close()
        mock_cancel.assert_called_once()

    def test_close_cancels_pending_motion_timer(self, mock_hw: MagicMock) -> None:
        pir = PIRSensor(pin=26)
        pir._warmup_complete()
        pir._handle_motion()
        assert pir._pending_timer is not None
        pir.close()
        assert pir._pending_timer is None
