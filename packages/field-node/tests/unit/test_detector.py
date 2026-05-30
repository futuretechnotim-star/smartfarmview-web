"""Unit tests for ObjectDetector — mocks TFLite interpreter, no model file required."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from field_node.detector import INTERESTING_LABELS, ObjectDetector


def _make_interpreter(scores, classes, boxes):
    """Build a mock TFLite interpreter returning the given detection tensors."""
    interp = MagicMock()
    interp.get_input_details.return_value = [{"index": 0, "shape": [1, 300, 300, 3]}]
    interp.get_output_details.return_value = [
        {"index": 1},  # boxes
        {"index": 2},  # classes
        {"index": 3},  # scores
    ]
    interp.get_tensor.side_effect = lambda idx: {
        1: np.array([boxes]),
        2: np.array([classes]),
        3: np.array([scores]),
    }[idx]
    return interp


def _make_detector(tmp_path: Path, scores, classes, boxes, labels: list[str]) -> ObjectDetector:
    model_file = tmp_path / "detect.tflite"
    labels_file = tmp_path / "labelmap.txt"
    model_file.write_bytes(b"fake")
    labels_file.write_text("\n".join(labels))

    interp = _make_interpreter(scores, classes, boxes)

    with (
        patch("field_node.detector._load_interpreter", return_value=interp),
        patch("field_node.config.settings.detector_model_path", str(model_file)),
        patch("field_node.config.settings.detector_labels_path", str(labels_file)),
        patch("field_node.config.settings.detector_min_confidence", 0.5),
    ):
        return ObjectDetector()


def _tiny_jpeg() -> bytes:
    """Minimal valid JPEG for Pillow to open (1×1 white pixel)."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def jpeg():
    return _tiny_jpeg()


class TestInterestingLabels:
    def test_person_in_set(self):
        assert "person" in INTERESTING_LABELS

    def test_vehicle_labels_in_set(self):
        for label in ("car", "truck", "bus", "motorcycle"):
            assert label in INTERESTING_LABELS

    def test_animal_labels_in_set(self):
        for label in ("dog", "cat", "horse", "cow", "bear"):
            assert label in INTERESTING_LABELS

    def test_uninteresting_labels_absent(self):
        for label in ("chair", "laptop", "bottle", "fork"):
            assert label not in INTERESTING_LABELS


class TestDetector:
    def test_returns_person_above_threshold(self, tmp_path, jpeg):
        det = _make_detector(
            tmp_path,
            scores=[0.9, 0.3],
            classes=[0, 1],  # person, bicycle
            boxes=[[0.1, 0.2, 0.8, 0.7], [0.0, 0.0, 0.5, 0.5]],
            labels=["person", "bicycle"],
        )
        with (
            patch("field_node.config.settings.detector_min_confidence", 0.5),
            patch("field_node.config.settings.detector_model_path", det._interp.__class__.__name__),
        ):
            results, ms = det.detect(jpeg)

        assert len(results) == 1
        assert results[0].label == "person"
        assert results[0].confidence == pytest.approx(0.9, abs=0.01)

    def test_suppresses_below_confidence_threshold(self, tmp_path, jpeg):
        det = _make_detector(
            tmp_path,
            scores=[0.3],
            classes=[0],
            boxes=[[0.0, 0.0, 1.0, 1.0]],
            labels=["person"],
        )
        with patch("field_node.config.settings.detector_min_confidence", 0.5):
            results, _ = det.detect(jpeg)
        assert results == []

    def test_suppresses_uninteresting_labels(self, tmp_path, jpeg):
        det = _make_detector(
            tmp_path,
            scores=[0.95],
            classes=[0],
            boxes=[[0.0, 0.0, 1.0, 1.0]],
            labels=["chair"],
        )
        with patch("field_node.config.settings.detector_min_confidence", 0.5):
            results, _ = det.detect(jpeg)
        assert results == []

    def test_strips_background_label(self, tmp_path, jpeg):
        # Label map with ??? background at index 0; person is index 1 in file but 0 in model output
        det = _make_detector(
            tmp_path,
            scores=[0.8],
            classes=[0],  # model class 0 = person after stripping ???
            boxes=[[0.0, 0.0, 1.0, 1.0]],
            labels=["???", "person"],  # ??? stripped, so labels[0] = person
        )
        with patch("field_node.config.settings.detector_min_confidence", 0.5):
            results, _ = det.detect(jpeg)
        assert len(results) == 1
        assert results[0].label == "person"

    def test_sorted_by_confidence_descending(self, tmp_path, jpeg):
        det = _make_detector(
            tmp_path,
            scores=[0.6, 0.9],
            classes=[0, 1],  # person, car
            boxes=[[0.0, 0.0, 0.5, 0.5], [0.5, 0.5, 1.0, 1.0]],
            labels=["person", "car"],
        )
        with patch("field_node.config.settings.detector_min_confidence", 0.5):
            results, _ = det.detect(jpeg)
        assert results[0].confidence > results[1].confidence

    def test_returns_inference_ms(self, tmp_path, jpeg):
        det = _make_detector(
            tmp_path,
            scores=[0.9],
            classes=[0],
            boxes=[[0.0, 0.0, 1.0, 1.0]],
            labels=["person"],
        )
        with patch("field_node.config.settings.detector_min_confidence", 0.5):
            _, ms = det.detect(jpeg)
        assert isinstance(ms, int)
        assert ms >= 0
