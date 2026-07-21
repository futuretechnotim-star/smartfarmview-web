"""Pure decision logic for the "graceful shutdown before OTA" sequence.

An OTA update ends in machine.reset() — a real chip reset that leaves the
relay control pins undriven for a brief window before main.py reasserts them.
Until the relay's own NC/fail-safe wiring makes that harmless, an OTA update
must not proceed until the gateway has actually halted (halt_confirmed), so
the Pico's own reboot glitch doesn't land on a live, running Pi.

No imports -> runs on MicroPython and CPython (pytest) alike.
"""

WAIT = "wait"
PROCEED = "proceed"
ABORT = "abort"


def decide_ota_wait(halt_confirmed: bool, elapsed_s: float, timeout_s: float) -> str:
    """Return WAIT, PROCEED, or ABORT for the current inputs.

    PROCEED once the gateway confirms it has actually halted. ABORT if that
    never happens within timeout_s — leaving existing firmware in place is
    safer than guessing the gateway is down and risking an unclean cut.
    """
    if halt_confirmed:
        return PROCEED
    if elapsed_s >= timeout_s:
        return ABORT
    return WAIT
