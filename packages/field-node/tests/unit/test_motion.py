from unittest.mock import MagicMock, patch

import pytest

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
            mock_cls.assert_called_once_with(17)

    def test_uses_settings_pin_when_none_given(self) -> None:
        with patch("field_node.motion.MotionSensor") as mock_cls:
            mock_cls.return_value.motion_detected = False
            pir = PIRSensor()
            pir.close()
            # settings.pir_gpio_pin default is 26
            mock_cls.assert_called_once_with(26)


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


class TestMotionAfterWarmup:
    def test_on_motion_callback_called(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        cb = MagicMock()
        pir.on_motion = cb
        pir._handle_motion()
        cb.assert_called_once()

    def test_on_clear_callback_called(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        cb = MagicMock()
        pir.on_clear = cb
        pir._handle_clear()
        cb.assert_called_once()

    def test_last_detected_at_set_on_motion(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        pir._handle_motion()
        assert pir.last_detected_at is not None

    def test_last_detected_at_not_updated_on_clear(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        pir._handle_clear()
        assert pir.last_detected_at is None

    def test_is_detected_reflects_hardware_state(self, pir: PIRSensor, mock_hw: MagicMock) -> None:
        mock_hw.motion_detected = True
        assert pir.is_detected is True
        mock_hw.motion_detected = False
        assert pir.is_detected is False

    def test_no_callback_set_does_not_raise(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        pir._handle_motion()  # on_motion is None — should not raise
        pir._handle_clear()  # on_clear is None — should not raise

    def test_callback_exception_does_not_propagate(self, pir: PIRSensor) -> None:
        pir._warmup_complete()
        pir.on_motion = MagicMock(side_effect=RuntimeError("boom"))
        pir._handle_motion()  # should not raise


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
