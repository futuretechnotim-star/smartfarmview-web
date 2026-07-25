"""Bounded local event log for the Pico watchdog.

Separate from MQTT telemetry, which is useless whenever the broker is
unreachable — exactly the condition under which local troubleshooting matters
most (e.g. distinguishing a low-voltage cutoff from a heartbeat-timeout
reboot after the fact, with nothing but the Pico's own flash to go on).

Rotates a single active file into a ``.1`` backup once it crosses
``max_bytes``, so total on-flash usage stays bounded to roughly
``2 * max_bytes`` no matter how long the watchdog runs. Uses only ``open``/
``os.stat``/``os.rename``/``os.remove``, which behave the same under
MicroPython's VFS and CPython, so this is tested directly against a real
filesystem (no hardware mocking needed).

Timestamps are seconds since boot (no RTC/NTP sync elsewhere in this
firmware), not wall-clock — fine for reconstructing relative event order and
spacing (e.g. "cuts landing ~300s apart" points at the heartbeat timeout, not
the voltage threshold).
"""

import os
import time


class RollingLog:
    def __init__(self, path: str, max_bytes: int = 16384) -> None:
        self._path = path
        self._backup_path = path + ".1"
        self._max_bytes = max_bytes

    def _size(self) -> int:
        try:
            return os.stat(self._path)[6]
        except OSError:
            return 0

    def _rotate_if_needed(self) -> None:
        if self._size() < self._max_bytes:
            return
        try:  # noqa: SIM105 — contextlib.suppress isn't available on this MicroPython build
            os.remove(self._backup_path)
        except OSError:
            pass
        try:  # noqa: SIM105 — contextlib.suppress isn't available on this MicroPython build
            os.rename(self._path, self._backup_path)
        except OSError:
            pass  # e.g. path didn't exist, nothing to rotate

    def append(self, message: str) -> None:
        """Best-effort — logging must never block or crash the safety loop."""
        self._rotate_if_needed()
        line = f"{time.time():.1f} {message}\n"
        try:  # noqa: SIM105 — contextlib.suppress isn't available on this MicroPython build
            with open(self._path, "a") as f:
                f.write(line)
        except OSError:
            pass
