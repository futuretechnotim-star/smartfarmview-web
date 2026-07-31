import json
from unittest.mock import MagicMock, patch

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
        patch("field_node.telemetry._wifi_rssi_dbm", return_value=-52.0),
    ):
        publisher.publish_heartbeat()

    telemetry_calls = [c for c in mock_mqtt.publish.call_args_list if "/telemetry" in c[0][0]]
    assert len(telemetry_calls) == 1
    topic, payload_str = telemetry_calls[0][0][:2]
    payload = json.loads(payload_str)
    assert "securitymesh/" in topic
    assert payload["cpu_temp"] == 45.0
    assert payload["storage_pct"] == 12.5
    assert payload["wifi_rssi_dbm"] == -52.0


def test_publish_skipped_when_not_connected(mock_mqtt):
    pub = TelemetryPublisher()
    pub._connected = False
    with (
        patch("field_node.telemetry._cpu_temp", return_value=45.0),
        patch("field_node.telemetry._storage_percent", return_value=12.5),
        patch("field_node.telemetry._wifi_rssi_dbm", return_value=-52.0),
    ):
        pub.publish_heartbeat()
    mock_mqtt.publish.assert_not_called()


def test_publish_motion_event(publisher, mock_mqtt):
    publisher.publish_motion_event("/opt/field-node/captures/test.jpg")
    topics = [c[0][0] for c in mock_mqtt.publish.call_args_list]
    assert any("/motion_state" in t for t in topics)
    assert any("/motion" in t for t in topics)


def test_publish_motion_clear(publisher, mock_mqtt):
    publisher.publish_motion_clear()
    mock_mqtt.publish.assert_called_once()
    topic, payload_str = mock_mqtt.publish.call_args[0][:2]
    assert "/motion_state" in topic
    assert payload_str == "OFF"


def test_discovery_publishes_all_entities(publisher, mock_mqtt):
    mock_mqtt.publish.reset_mock()
    publisher.publish_discovery()
    assert mock_mqtt.publish.call_count == 17
    topics = [c[0][0] for c in mock_mqtt.publish.call_args_list]
    assert any("cpu_temp" in t for t in topics)
    assert any("storage_pct" in t for t in topics)
    assert any("wifi_signal" in t for t in topics)
    assert any("motion" in t for t in topics)
    assert any("battery_voltage" in t for t in topics)
    assert any("battery_current" in t for t in topics)
    assert any("battery_power" in t for t in topics)
    assert any("battery_soc" in t for t in topics)
    assert any("battery_runtime" in t for t in topics)
    assert any("power_mode" in t for t in topics)
    assert any("snapshot" in t for t in topics)
    assert any("capture" in t for t in topics)
    assert any("solar_net_avg_ma" in t for t in topics)
    assert any("solar_projected_eod_soc" in t for t in topics)
    assert any("solar_deficit_pct" in t for t in topics)
    assert any("detection_log" in t for t in topics)


def test_publish_snapshot(publisher, mock_mqtt):
    jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 100
    publisher.publish_snapshot(jpeg_bytes)
    mock_mqtt.publish.assert_called_once()
    topic = mock_mqtt.publish.call_args[0][0]
    payload = mock_mqtt.publish.call_args[0][1]
    assert "/snapshot" in topic
    assert payload == jpeg_bytes


def test_on_command_capture_calls_handler(mock_mqtt):
    handler = MagicMock()
    pub = TelemetryPublisher(on_command=handler)
    pub._connected = True

    msg = MagicMock()
    msg.payload = json.dumps({"cmd": "capture"}).encode()
    pub._on_message(mock_mqtt, None, msg)

    handler.assert_called_once_with({"cmd": "capture"})


def test_publish_detection_log_sends_to_correct_topic(publisher, mock_mqtt):
    events = [
        {
            "id": "abc-123",
            "detectedAt": "2026-06-02T10:00:00+00:00",
            "summary": "deer (87%)",
            "objects": [{"label": "deer", "confidence": 0.87}],
            "imageFilename": "abc-123.jpg",
        }
    ]
    publisher.publish_detection_log(events)

    mock_mqtt.publish.assert_called_once()
    topic, payload_str, *_ = mock_mqtt.publish.call_args[0]
    payload = json.loads(payload_str)

    assert "/detection_log" in topic
    assert payload["state"] == "1 detection"
    assert payload["detections"] == events


def test_publish_detection_log_pluralises_state(publisher, mock_mqtt):
    events = [{"id": str(i)} for i in range(3)]
    publisher.publish_detection_log(events)
    payload = json.loads(mock_mqtt.publish.call_args[0][1])
    assert payload["state"] == "3 detections"


def test_publish_detection_log_retained(publisher, mock_mqtt):
    publisher.publish_detection_log([])
    kwargs = mock_mqtt.publish.call_args[1]
    assert kwargs.get("retain") is True


def test_publish_detection_log_skipped_when_not_connected(mock_mqtt):
    pub = TelemetryPublisher()
    pub._connected = False
    pub.publish_detection_log([{"id": "x"}])
    mock_mqtt.publish.assert_not_called()
