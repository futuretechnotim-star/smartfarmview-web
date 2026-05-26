from unittest.mock import patch

import pytest


@pytest.fixture
def mock_camera():
    with patch("field_node.camera.Picamera2") as mock:
        yield mock.return_value


@pytest.fixture
def mock_mqtt():
    with patch("field_node.telemetry.mqtt.Client") as mock:
        yield mock.return_value
