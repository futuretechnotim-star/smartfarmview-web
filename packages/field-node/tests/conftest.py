import sys
from unittest.mock import MagicMock, patch

import pytest

# picamera2 is Pi-only and cannot be installed on macOS / CI.
# Stub the entire package before any test module is imported so that
# `from field_node.camera import Camera` (and any code that imports main)
# doesn't raise ModuleNotFoundError.
for _mod in ("picamera2", "picamera2.encoders", "picamera2.outputs"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


@pytest.fixture
def mock_camera():
    with patch("field_node.camera.Picamera2") as mock:
        yield mock.return_value


@pytest.fixture
def mock_mqtt():
    with patch("field_node.telemetry.mqtt.Client") as mock:
        yield mock.return_value
