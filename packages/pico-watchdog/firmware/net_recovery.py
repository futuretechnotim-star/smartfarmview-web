"""Pure connectivity self-heal timeout decision for the Pico watchdog.

Same shape as gate_logic.py / fan_logic.py: no I/O, no imports, runs
identically on MicroPython and CPython under pytest. main.py's loop tracks
how long MQTT has been unreachable and calls should_reboot() each tick to
decide whether to pulse the PSU relay and reset (see main.py's
"Connectivity self-heal" block for the full rationale).

Two thresholds instead of one: wifi up but MQTT down points at a broker/HA-
side hiccup (Mosquitto add-on restarting, an HA update in progress, Docker
churn) rather than a wedged radio or a genuinely hung Pi — the Pico's own
network stack is proven working, it's just the far end that's quiet. That
case gets a much longer leash (default 30 min) before the disruptive
power-cycle. Wifi also down means the Pico can't reach anything at all,
a stronger signal of a real problem, so it keeps the shorter default
(15 min) — unchanged from before this distinction was added, so existing
tuned behavior for that case doesn't shift.
"""


def should_reboot(
    offline_s: float,
    wifi_connected: bool,
    *,
    timeout_wifi_down_s: float,
    timeout_wifi_up_s: float,
) -> bool:
    """Return True once ``offline_s`` (seconds since MQTT was last reachable)
    has exceeded the threshold appropriate to the current wifi state."""
    timeout = timeout_wifi_up_s if wifi_connected else timeout_wifi_down_s
    return offline_s >= timeout
