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

import os

import machine  # type: ignore[import-not-found]
import ujson  # type: ignore[import-not-found]
import urequests  # type: ignore[import-not-found]


def apply_update(base_url: str) -> bool:
    """Download ``manifest.json`` from ``base_url`` and replace each listed file.
    Returns False on any error (leaving existing firmware intact)."""
    try:
        resp = urequests.get(base_url + "manifest.json")
        manifest = ujson.loads(resp.text)
        resp.close()
    except Exception:  # noqa: BLE001
        return False

    files = manifest.get("files", [])
    for name in files:
        try:
            r = urequests.get(base_url + name)
            content = r.content
            r.close()
        except Exception:  # noqa: BLE001
            return False
        # Write to a temp file first, then rename, to avoid a half-written module.
        tmp = name + ".tmp"
        with open(tmp, "wb") as f:
            f.write(content)
        os.rename(tmp, name)

    machine.reset()
    return True
