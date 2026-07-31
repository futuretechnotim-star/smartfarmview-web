"""Pure connectivity-watchdog decision logic.

Linux analogue of the Pico's NET_RECOVERY_TIMEOUT_S self-heal (see
pico-watchdog/firmware/main.py and docs/watchdog-bench-test.md "Findings —
2026-07-28"): paho's loop_start() auto-reconnect and field-node.service's
Restart=always only recover a crashed process, not a wedged OS-level WiFi
link (wpa_supplicant can sit associated-but-dead, or fail to re-associate
after the gateway's AP drops). Neither is a process crash, so neither gets
caught by systemd.

This module only decides *when* that's happened; main.py executes the
actual `sudo reboot`.
"""


def should_reboot(*, connected: bool, seconds_since_connected: float, timeout_s: float) -> bool:
    """True once the broker has been unreachable long enough to assume the
    Pi's own network stack — not just a momentary broker blip — is wedged,
    and an unattended reboot is the only remaining recovery path."""
    if connected:
        return False
    return seconds_since_connected >= timeout_s
