"""Firmware version stamp, so a boot log entry or a telemetry snapshot says
exactly what code is running — without this, confirming an OTA actually
activated meant cross-referencing MQTT reconnect timing against battery
voltage history (see docs/pico-watchdog.md's 2026-08-06 OTA notes).

This file's VERSION is overwritten by scripts/build_ota_manifest.py right
before it builds manifest.json, using the repo's git short SHA (+ "-dirty" if
there are uncommitted changes) — so it always reflects exactly what was
staged for the OTA, no separate manual bump to remember or forget. This
default value is only ever seen on a from-scratch flash that skipped the
manifest-build step (e.g. straight `mpremote fs cp`).
"""

VERSION = "unknown"
