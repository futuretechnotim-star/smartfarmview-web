"""Tests for detection log helpers in main.py."""

import pytest

from field_node.config import settings
from field_node.main import (
    _delete_detection_image,
    _load_detection_log,
    _persist_detection_log,
    _store_detection_image,
)


@pytest.fixture(autouse=True)
def reset_store_dir(tmp_path, monkeypatch):
    """Point detection_store_dir at a temp dir for every test."""
    monkeypatch.setattr("field_node.main.settings.detection_store_dir", str(tmp_path))
    monkeypatch.setattr("field_node.main.settings.detection_store_count", 3)
    return tmp_path


class TestStoreDetectionImage:
    def test_writes_jpeg_and_returns_ha_relative_path(self, tmp_path):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 50
        # Returns the HA-media-relative path (so the LandPlan API can proxy it
        # directly); the file itself is written as the bare {id}.jpg in the store.
        result = _store_detection_image("test-uuid-1234", jpeg)
        assert result == f"landplan/{settings.node_id}/test-uuid-1234.jpg"
        assert (tmp_path / "test-uuid-1234.jpg").read_bytes() == jpeg

    def test_returns_none_when_store_dir_empty(self, monkeypatch):
        monkeypatch.setattr("field_node.main.settings.detection_store_dir", "")
        result = _store_detection_image("some-id", b"data")
        assert result is None

    def test_returns_none_on_write_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr("field_node.main.settings.detection_store_dir", "/nonexistent/path/xyz")
        result = _store_detection_image("some-id", b"data")
        assert result is None


class TestDeleteDetectionImage:
    def test_deletes_existing_file(self, tmp_path):
        (tmp_path / "abc.jpg").write_bytes(b"data")
        _delete_detection_image({"imageFilename": "abc.jpg"})
        assert not (tmp_path / "abc.jpg").exists()

    def test_no_error_when_file_missing(self, tmp_path):
        # Should not raise
        _delete_detection_image({"imageFilename": "nonexistent.jpg"})

    def test_no_error_when_filename_is_none(self):
        _delete_detection_image({"imageFilename": None})

    def test_no_op_when_store_dir_empty(self, monkeypatch):
        monkeypatch.setattr("field_node.main.settings.detection_store_dir", "")
        _delete_detection_image({"imageFilename": "abc.jpg"})  # must not raise


class TestPersistAndLoadDetectionLog:
    def test_round_trip(self, tmp_path):
        events = [{"id": "x", "summary": "deer"}]
        _persist_detection_log(events)
        loaded = _load_detection_log()
        assert loaded == events

    def test_load_returns_empty_when_no_file(self, tmp_path):
        result = _load_detection_log()
        assert result == []

    def test_load_returns_empty_on_corrupt_json(self, tmp_path):
        (tmp_path / "detection_log.json").write_text("not-json{{{")
        result = _load_detection_log()
        assert result == []

    def test_load_returns_empty_when_store_dir_not_configured(self, monkeypatch):
        monkeypatch.setattr("field_node.main.settings.detection_store_dir", "")
        assert _load_detection_log() == []

    def test_persist_no_op_when_store_dir_not_configured(self, monkeypatch, tmp_path):
        monkeypatch.setattr("field_node.main.settings.detection_store_dir", "")
        _persist_detection_log([{"id": "y"}])
        # No file should have been written anywhere near tmp_path
        assert not (tmp_path / "detection_log.json").exists()


class TestDetectionLogEviction:
    """Test that the rolling list trims correctly and old image files are deleted."""

    def test_evicts_oldest_when_full(self, tmp_path):
        """When log is at capacity, prepending a new event removes the oldest."""
        # Pre-populate log and corresponding image files
        detection_log: list[dict] = []
        for i in range(3):  # detection_store_count == 3
            fname = f"old-{i}.jpg"
            (tmp_path / fname).write_bytes(b"img")
            detection_log.insert(0, {"id": f"id-{i}", "imageFilename": fname})

        # Simulate what on_motion does when log is full
        new_id = "new-uuid"
        evicted = detection_log.pop()  # oldest (id-0)
        _delete_detection_image(evicted)
        detection_log.insert(0, {"id": new_id, "imageFilename": f"{new_id}.jpg"})

        # The oldest file should be gone; newer files survive
        assert not (tmp_path / "old-0.jpg").exists()
        assert (tmp_path / "old-1.jpg").exists()
        assert (tmp_path / "old-2.jpg").exists()
        assert len(detection_log) == 3
        assert detection_log[0]["id"] == new_id

    def test_does_not_exceed_store_count(self, tmp_path):
        detection_log: list[dict] = []
        for i in range(5):
            if len(detection_log) >= 3:
                evicted = detection_log.pop()
                _delete_detection_image(evicted)
            detection_log.insert(0, {"id": f"id-{i}", "imageFilename": None})
        assert len(detection_log) == 3
