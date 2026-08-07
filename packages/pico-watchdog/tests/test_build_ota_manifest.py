"""Tests for the OTA manifest/version-stamp build script."""

from pathlib import Path

from build_ota_manifest import build_manifest, format_version, write_version_file


class TestFormatVersion:
    def test_clean_checkout(self):
        assert format_version("abc1234", dirty=False) == "abc1234"

    def test_dirty_checkout(self):
        assert format_version("abc1234", dirty=True) == "abc1234-dirty"


class TestBuildManifest:
    def test_lists_py_files_excluding_secrets(self, tmp_path: Path):
        for name in ["main.py", "config.py", "secrets.py", "secrets.example.py"]:
            (tmp_path / name).write_text("")
        manifest = build_manifest(tmp_path)
        assert manifest == {"files": ["config.py", "main.py"]}

    def test_empty_dir(self, tmp_path: Path):
        assert build_manifest(tmp_path) == {"files": []}


class TestWriteVersionFile:
    def test_writes_version_constant(self, tmp_path: Path):
        write_version_file(tmp_path, "abc1234-dirty")
        content = (tmp_path / "firmware_version.py").read_text()
        assert content == 'VERSION = "abc1234-dirty"\n'
