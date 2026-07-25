"""Tests for the bounded local event log."""

from local_log import RollingLog


def test_append_writes_line(tmp_path):
    log = RollingLog(str(tmp_path / "watchdog.log"), max_bytes=1024)
    log.append("boot")
    content = (tmp_path / "watchdog.log").read_text()
    assert content.endswith("boot\n")
    assert content.split(" ", 1)[0].replace(".", "", 1).isdigit()  # timestamp prefix


def test_append_multiple_lines_preserves_order(tmp_path):
    log = RollingLog(str(tmp_path / "watchdog.log"), max_bytes=1024)
    log.append("first")
    log.append("second")
    lines = (tmp_path / "watchdog.log").read_text().splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("first")
    assert lines[1].endswith("second")


def test_rotates_into_backup_once_over_limit(tmp_path):
    path = tmp_path / "watchdog.log"
    backup = tmp_path / "watchdog.log.1"
    log = RollingLog(str(path), max_bytes=50)

    for i in range(20):
        log.append(f"line-{i}")

    assert path.exists()
    assert backup.exists()
    # Active file never grows unbounded: it only ever holds what's been
    # written since the last rotation check, which is capped by max_bytes.
    assert path.stat().st_size < 50 + 64  # one line's worth of slack


def test_no_backup_before_first_rotation(tmp_path):
    path = tmp_path / "watchdog.log"
    backup = tmp_path / "watchdog.log.1"
    log = RollingLog(str(path), max_bytes=1024)
    log.append("only a little data")
    assert path.exists()
    assert not backup.exists()


def test_missing_file_does_not_raise_on_append(tmp_path):
    log = RollingLog(str(tmp_path / "nested" / "watchdog.log"), max_bytes=1024)
    # Parent dir doesn't exist — append() must swallow the OSError, not crash
    # the caller's safety loop.
    log.append("should not raise")
