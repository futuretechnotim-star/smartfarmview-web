"""Over-the-air firmware update for the Pico watchdog (MicroPython).

Split into two phases, because the update files are served BY the gateway
itself:

* stage_update() downloads manifest.json + each listed file and writes each
  to <name>.tmp — NOT yet activated. Needs the gateway's own network/AP to
  still be up, so main.py runs this BEFORE ever asking the gateway to halt.
* activate_staged() renames each staged file into place and resets — pure
  filesystem work, no network needed. main.py only calls this once the
  gateway has actually halted (halt_confirmed), since this reset briefly
  leaves the relay pins undriven, and until the relay's own wiring is
  fail-safe (see docs/gateway-node.md), it must not land on a live, running
  Pi.

Staging and activating in one step doesn't work here: by the time it's safe
to reset (gateway halted), the gateway's AP has gone down with it, so the
file server that a single-step apply would still be trying to reach is
already unreachable. Splitting the two lets the download happen while the
network is up and the reset happen only once it's genuinely safe.

Triggered remotely via the MQTT command topic
(``{"cmd": "ota", "base_url": "http://<gateway>/pico-fw/"}``) — remote
reachability is brokered through the gateway Pi (which is on Tailscale),
since the Pico cannot join a tailnet directly (see docs/pico-watchdog.md).
Validate on hardware before relying on it in the field — a bad flash needs
physical recovery.
"""

import gc
import os

import machine  # type: ignore[import-not-found]
import ujson  # type: ignore[import-not-found]
import urequests  # type: ignore[import-not-found]

_STAGED_LIST = "ota_staged.json"  # names of files successfully staged as <name>.tmp


def stage_update(base_url: str) -> "str | None":
    """Download manifest.json + each listed file from base_url to <name>.tmp.

    Returns ``None`` on success, an error string on any failure (nothing is
    activated either way until activate_staged() runs, so a failed/partial
    stage leaves current firmware untouched).

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
    staged = []
    for name in files:
        gc.collect()
        try:
            r = urequests.get(base_url + name)
            content = r.content
            r.close()
        except Exception as e:  # noqa: BLE001
            return f"download_failed({name}): {e}"
        try:
            with open(name + ".tmp", "wb") as f:
                f.write(content)
        except Exception as e:  # noqa: BLE001
            return f"write_failed({name}): {e}"
        staged.append(name)
        del content
        gc.collect()

    try:
        with open(_STAGED_LIST, "w") as f:
            ujson.dump(staged, f)
    except Exception as e:  # noqa: BLE001
        return f"staged_list_write_failed: {e}"

    return None


def activate_staged() -> "str | None":
    """Rename each staged <name>.tmp into place and reset.

    Pure filesystem work — no network needed, safe to run once the gateway
    (and its AP) has already gone down. Returns an error string if nothing
    was staged; never returns on success (ends in machine.reset()).
    """
    try:
        with open(_STAGED_LIST) as f:
            staged = ujson.load(f)
    except Exception as e:  # noqa: BLE001
        return f"no_staged_update: {e}"

    for name in staged:
        try:
            os.rename(name + ".tmp", name)
        except Exception as e:  # noqa: BLE001
            return f"activate_failed({name}): {e}"

    os.remove(_STAGED_LIST)
    machine.reset()
    return None
