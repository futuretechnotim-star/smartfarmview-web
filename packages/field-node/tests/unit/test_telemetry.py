import json
from unittest.mock import patch

import pytest

from field_node.telemetry import TelemetryPublisher


@pytest.fixture
def publisher(mock_mqtt):
    pub = TelemetryPublisher()
    pub._connected = True
    return pub


def test_publish_heartbeat_sends_to_correct_topic(publisher, mock_mqtt):
    with (
        patch("field_node.telemetry._cpu_temp", return_value=45.0),
        patch("field_node.telemetry._storage_percent", return_value=12.5),
    ):
        publisher.publish_heartbeat()

    mock_mqtt.publish.assert_called_once()
    topic, payload_str = mock_mqtt.publish.call_args[0][:2]
    payload = json.loads(payload_str)
    assert "securitymesh/" in topic
    assert "/telemetry" in topic
    assert payload["cpu_temp"] == 45.0
    assert payload["storage_pct"] == 12.5


def test_publish_skipped_when_not_connected(mock_mqtt):
    pub = TelemetryPublisher()
    pub._connected = False
    pub.publish_heartbeat()
    mock_mqtt.publish.assert_not_called()


def test_publish_motion_event(publisher, mock_mqtt):
    publisher.publish_motion_event("/opt/field-node/captures/test.jpg")
    mock_mqtt.publish.assert_called_once()
    topic = mock_mqtt.publish.call_args[0][0]
    assert "/motion" in topic
