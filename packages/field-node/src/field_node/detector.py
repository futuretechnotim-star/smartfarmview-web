"""
TFLite object detector using COCO SSD MobileNet V1 INT8.

Runs inference only on demand (called from motion callbacks, not continuously),
so power impact is limited to a 1–2 second CPU spike per event.

Model: coco_ssd_mobilenet_v1_1.0_quant — 300×300 input, 80 COCO classes.
Labels: standard COCO labelmap.txt shipped with the model zip.
"""

import io
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import structlog
from PIL import Image

from field_node.config import settings

log = structlog.get_logger()

# COCO classes we care about for security/farm monitoring.
# Everything else (furniture, food, sports equipment, etc.) is silently ignored.
INTERESTING_LABELS: frozenset[str] = frozenset(
    {
        "person",
        "bicycle",
        "car",
        "motorcycle",
        "bus",
        "truck",
        "bird",
        "cat",
        "dog",
        "horse",
        "sheep",
        "cow",
        "elephant",
        "bear",
        "zebra",
        "giraffe",
    }
)


@dataclass
class Detection:
    label: str
    confidence: float
    # Normalised bounding box [ymin, xmin, ymax, xmax] in 0–1 range
    bbox: tuple[float, float, float, float]


def _load_interpreter(model_path: str) -> object:
    """Import TFLite interpreter from whichever package is installed."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        from tflite_runtime.interpreter import Interpreter
    interp = Interpreter(model_path=model_path)
    interp.allocate_tensors()
    return interp


class ObjectDetector:
    def __init__(self) -> None:
        model_path = settings.detector_model_path
        labels_path = settings.detector_labels_path

        self._interp = _load_interpreter(model_path)

        input_details = self._interp.get_input_details()  # type: ignore[attr-defined]
        self._input_idx: int = input_details[0]["index"]
        self._input_h: int = int(input_details[0]["shape"][1])
        self._input_w: int = int(input_details[0]["shape"][2])

        output_details = self._interp.get_output_details()  # type: ignore[attr-defined]
        # Standard SSD output order: boxes, classes, scores, count
        self._boxes_idx: int = output_details[0]["index"]
        self._classes_idx: int = output_details[1]["index"]
        self._scores_idx: int = output_details[2]["index"]

        raw_labels = Path(labels_path).read_text().splitlines()
        # Some label files include a background class ("???" or "") at index 0 — strip it.
        if raw_labels and raw_labels[0].strip() in ("", "???"):
            raw_labels = raw_labels[1:]
        self._labels: list[str] = [lbl.strip() for lbl in raw_labels]

        log.info(
            "detector_ready",
            model=model_path,
            input_size=f"{self._input_w}×{self._input_h}",
            labels=len(self._labels),
        )

    def detect(self, jpeg_bytes: bytes) -> tuple[list[Detection], int]:
        """Run inference on a JPEG image.

        Returns (detections, inference_ms). Only returns objects whose label is
        in INTERESTING_LABELS and whose confidence meets the configured threshold.
        Results are sorted by confidence descending.
        """
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        img_resized = img.resize((self._input_w, self._input_h), Image.Resampling.BILINEAR)
        input_array = np.expand_dims(np.array(img_resized, dtype=np.uint8), axis=0)

        self._interp.set_tensor(self._input_idx, input_array)  # type: ignore[attr-defined]

        t0 = time.monotonic()
        self._interp.invoke()  # type: ignore[attr-defined]
        inference_ms = round((time.monotonic() - t0) * 1000)

        boxes: np.ndarray = self._interp.get_tensor(self._boxes_idx)[0]  # type: ignore[attr-defined]
        classes: np.ndarray = self._interp.get_tensor(self._classes_idx)[0]  # type: ignore[attr-defined]
        scores: np.ndarray = self._interp.get_tensor(self._scores_idx)[0]  # type: ignore[attr-defined]

        results: list[Detection] = []
        for i in range(len(scores)):
            score = float(scores[i])
            if score < settings.detector_min_confidence:
                continue
            label_idx = int(classes[i])
            if label_idx >= len(self._labels):
                continue
            label = self._labels[label_idx]
            if label not in INTERESTING_LABELS:
                continue
            ymin, xmin, ymax, xmax = (float(v) for v in boxes[i])
            results.append(
                Detection(
                    label=label,
                    confidence=round(score, 3),
                    bbox=(ymin, xmin, ymax, xmax),
                )
            )

        results.sort(key=lambda d: d.confidence, reverse=True)
        return results, inference_ms
