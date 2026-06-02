"""LiFePO4 resting-voltage → state-of-charge estimate (pure).

The Pico publishes ``soc_pct`` so the gateway power brain stays
chemistry-agnostic. LiFePO4 has a famously flat discharge curve, so a
voltage-only SoC is approximate — good enough to drive the graceful (software)
tier, while the gate's hard safety thresholds work on raw voltage. Re-tune the
curve from the field-soak dataset (docs/gateway-node.md).

No imports → runs on MicroPython and CPython (pytest) alike.
"""

# 12.8V (4S) LiFePO4, resting. Ordered high → low.
_CURVE = (
    (13.60, 100),
    (13.40, 99),
    (13.30, 90),
    (13.20, 80),
    (13.10, 60),
    (13.00, 40),
    (12.90, 30),
    (12.80, 20),
    (12.50, 10),
    (12.00, 0),
)


def lifepo4_soc(voltage_v: float) -> int:
    if voltage_v >= _CURVE[0][0]:
        return 100
    if voltage_v <= _CURVE[-1][0]:
        return 0
    for i in range(len(_CURVE) - 1):
        v_hi, soc_hi = _CURVE[i]
        v_lo, soc_lo = _CURVE[i + 1]
        if v_lo <= voltage_v <= v_hi:
            ratio = (voltage_v - v_lo) / (v_hi - v_lo)
            return int(round(soc_lo + ratio * (soc_hi - soc_lo)))
    return 0
