"""Pure enclosure-fan thermostat state machine.

Same shape as ``gate_logic.py``: given the current fan state and the enclosure
temperature, decide the next fan state. No I/O, no imports — runs identically
on MicroPython (on the Pico) and CPython (under pytest).

Hysteresis prevents relay chatter around a single threshold: the fan turns on
at ``on_temp_c`` and doesn't turn off again until temperature drops to
``off_temp_c`` (which must be lower).
"""

FAN_OFF = False
FAN_ON = True


def decide(fan_on: bool, temp_c: float, *, on_temp_c: float, off_temp_c: float) -> bool:
    """Return the next fan state (``True`` = relay energized/fan running)."""
    if fan_on:
        if temp_c <= off_temp_c:
            return FAN_OFF
        return FAN_ON

    if temp_c >= on_temp_c:
        return FAN_ON
    return FAN_OFF
