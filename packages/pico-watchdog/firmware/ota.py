"""Over-the-air firmware update for the Pico watchdog (MicroPython).

Pulls a manifest + the listed files from the gateway over HTTP and rewrites them
on the Pico's filesystem, then resets. Triggered remotely via the MQTT command
topic (``{"cmd": "ota", "base_url": "http://<gateway>/pico-fw/"}``) — remote
reachability is brokered through the gateway Pi (which is on Tailscale), since
the Pico cannot join a tailnet directly (see docs/pico-watchdog.md).

Safety: OTA only runs when the Pi is up and powered (the gateway serves the
files), never during a low-battery cutoff. Validate on hardware before relying
on it in the field — a bad flash needs physical recovery.
"""

import gc
import os

import machine  # type: ignore[import-not-found]
import ujson  # type: ignore[import-not-found]
import urequests  # type: ignore[import-not-found]


def apply_update(base_url: str) -> "str | None":
    """Download ``manifest.json`` from ``base_url`` and replace each listed file.

    Returns ``None`` on success — though the caller never actually sees that,
    since a successful update ends in ``machine.reset()``. Returns an error
    string on any failure (leaving existing firmware intact), so the caller
    can report *why* over telemetry instead of failing silently.

    Called from inside main.py's already-fully-loaded loop (WiFi + MQTT +
    sensor objects all still live) rather than a fresh interpreter, so RAM is
    tighter here than it looks in isolation — gc.collect() between downloads
    gives each file's buffer room instead of fragmenting into a MemoryError.
    """
    gc.collect()
    try:
        resp = urequests.get(base_url + "manifest.json")
        manifest = ujson.loads(resp.text)
        resp.close()
    except Exception as e:  # noqa: BLE001
        return f"manifest_fetch_failed: {e}"

    files = manifest.get("files", [])
    for name in files:
        gc.collect()
        try:
            r = urequests.get(base_url + name)
            content = r.content
            r.close()
        except Exception as e:  # noqa: BLE001
            return f"download_failed({name}): {e}"
        try:
            # Write to a temp file first, then rename, to avoid a half-written module.
            tmp = name + ".tmp"
            with open(tmp, "wb") as f:
                f.write(content)
            os.rename(tmp, name)
        except Exception as e:  # noqa: BLE001
            return f"write_failed({name}): {e}"
        del content
        gc.collect()

    machine.reset()
    return None
