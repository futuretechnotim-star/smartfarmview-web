"""Pure power-budget decision logic.

Extracted verbatim (behaviour-preserving) from the original
``field_node.power.manager`` so it can be shared between nodes. Functions here
are pure: callers pass in the current state-of-charge, a rolling net-current
average, and the time-of-day context, and get back a recommended power mode.
The stateful concerns (rolling window, clock, settings, actuation) stay in each
node's own ``PowerManager``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class PowerMode(enum.Enum):
    NORMAL = "normal"  # full operation
    ECO = "eco"  # WiFi PSM + CPU powersave + camera standby between captures
    LOW = "low"  # + slow telemetry
    CRITICAL = "critical"  # + camera disabled, minimal telemetry, prepare for shutdown


# Ordered least → most severe. Index = severity.
_SEVERITY: list[PowerMode] = [PowerMode.NORMAL, PowerMode.ECO, PowerMode.LOW, PowerMode.CRITICAL]

# SoC % thresholds to enter / leave each mode (5 % hysteresis prevents flapping).
# Thresholds are conservative: 10,000mAh LiFePO4 at rural deployment drains ~180mAh/h
# overnight, so the system must not treat 40% as "safe" — it may not see sun next day.
DEFAULT_ENTER_AT: dict[PowerMode, int] = {
    PowerMode.CRITICAL: 25,
    PowerMode.LOW: 50,
    PowerMode.ECO: 70,
}
DEFAULT_EXIT_AT: dict[PowerMode, int] = {
    PowerMode.CRITICAL: 30,
    PowerMode.LOW: 55,
    PowerMode.ECO: 75,
}

# Projected end-of-day SoC deficit → solar-driven mode escalation
# (deficit = min_overnight_soc - projected_eod_soc; ordered most → least severe).
DEFAULT_DEFICIT_THRESHOLDS: list[tuple[float, PowerMode]] = [
    (25.0, PowerMode.CRITICAL),
    (10.0, PowerMode.LOW),
    (0.0, PowerMode.ECO),
]

# Dawn recovery: if the battery woke below this SoC, hold LOW until it climbs back up.
# Prevents a depleted node from returning to full operation before it has recharged enough
# to survive a second bad day.
DEFAULT_DAWN_LOW_SOC: int = 50
DEFAULT_DAWN_RECOVERY_SOC: int = 65


@dataclass
class SolarStatus:
    is_daytime: bool
    net_avg_ma: float | None = None  # rolling avg of battery current (+ = charging)
    projected_eod_soc: float | None = None  # estimated SoC % at end of solar day
    deficit_pct: float | None = None  # min_overnight_soc - projected_eod_soc (>0 = shortfall)
    mode_reason: str = "soc"  # "soc" | "solar" | "solar_no_data"


def severity_index(mode: PowerMode) -> int:
    return _SEVERITY.index(mode)


def stricter(a: PowerMode, b: PowerMode | None) -> PowerMode:
    """Return the more severe of two modes (``b`` may be ``None``)."""
    if b is None:
        return a
    return _SEVERITY[max(severity_index(a), severity_index(b))]


def evaluate_soc_mode(
    current_soc_mode: PowerMode,
    soc_pct: int,
    *,
    enter_at: dict[PowerMode, int] | None = None,
    exit_at: dict[PowerMode, int] | None = None,
) -> PowerMode:
    """SoC-driven mode with hysteresis.

    ``current_soc_mode`` is the SoC component of the previous decision (tracked
    separately from the combined mode) so the entry/exit hysteresis is applied
    against the right baseline.
    """
    enter_at = enter_at if enter_at is not None else DEFAULT_ENTER_AT
    exit_at = exit_at if exit_at is not None else DEFAULT_EXIT_AT
    for mode in (PowerMode.CRITICAL, PowerMode.LOW, PowerMode.ECO):
        if current_soc_mode == mode:
            if soc_pct < exit_at[mode]:
                return mode
        elif soc_pct < enter_at[mode]:
            return mode
    return PowerMode.NORMAL


def compute_solar_mode(
    *,
    is_daytime: bool,
    soc_pct: int,
    net_avg_ma: float | None,
    hours_until_eod: float,
    battery_capacity_mah: int,
    min_overnight_soc: int,
    deficit_thresholds: list[tuple[float, PowerMode]] | None = None,
) -> tuple[PowerMode | None, SolarStatus]:
    """Project end-of-day SoC from the rolling net-current average and escalate
    the mode if the projected SoC won't clear the overnight reserve.

    Returns the solar-recommended mode (``None`` = no escalation) plus a
    populated :class:`SolarStatus` for telemetry.
    """
    thresholds = (
        deficit_thresholds if deficit_thresholds is not None else DEFAULT_DEFICIT_THRESHOLDS
    )

    if not is_daytime:
        return None, SolarStatus(is_daytime=False, net_avg_ma=net_avg_ma)

    if net_avg_ma is None:
        # Daytime but rolling window not yet populated — be mildly conservative.
        return PowerMode.ECO, SolarStatus(is_daytime=True, mode_reason="solar_no_data")

    remaining_mah = battery_capacity_mah * (soc_pct / 100.0)
    projected_mah = min(
        float(battery_capacity_mah),
        remaining_mah + net_avg_ma * hours_until_eod,
    )
    projected_soc = projected_mah / battery_capacity_mah * 100.0
    deficit = min_overnight_soc - projected_soc

    solar_mode: PowerMode | None = None
    for threshold, candidate in thresholds:
        if deficit > threshold:
            solar_mode = candidate
            break

    return solar_mode, SolarStatus(
        is_daytime=True,
        net_avg_ma=round(net_avg_ma, 1),
        projected_eod_soc=round(projected_soc, 1),
        deficit_pct=round(deficit, 1),
    )


def compute_night_mode(*, is_daytime: bool) -> PowerMode | None:
    """Return LOW during nighttime hours to enforce minimum power conservation.

    Camera is useless in the dark so there is no reason to run at NORMAL or ECO
    overnight. LOW gives: CPU powersave, WiFi PSM, and slow telemetry (300 s),
    without signalling a battery emergency (CRITICAL).
    """
    if is_daytime:
        return None
    return PowerMode.LOW


def compute_dawn_recovery_mode(
    *,
    dawn_soc_pct: int | None,
    current_soc_pct: int,
    dawn_low_threshold: int = DEFAULT_DAWN_LOW_SOC,
    recovery_threshold: int = DEFAULT_DAWN_RECOVERY_SOC,
) -> PowerMode | None:
    """Return LOW if the battery woke at dawn below the reserve and hasn't recovered.

    ``dawn_soc_pct`` is the SoC recorded at the start of the current solar day
    (``None`` before the first dawn transition — no constraint applied). While dawn
    was depleted and the battery hasn't climbed back to ``recovery_threshold``, the
    node is held at least at LOW to prioritise recharging over normal operation.
    """
    if dawn_soc_pct is None or dawn_soc_pct >= dawn_low_threshold:
        return None
    if current_soc_pct >= recovery_threshold:
        return None
    return PowerMode.LOW


def combine_modes(
    soc_mode: PowerMode,
    solar_mode: PowerMode | None,
    *,
    has_solar_data: bool,
) -> tuple[PowerMode, str]:
    """Combine the SoC-driven and solar-driven modes into the effective mode and
    a reason string for telemetry/logging.
    """
    new_mode = stricter(soc_mode, solar_mode)
    if solar_mode is not None and severity_index(solar_mode) > severity_index(soc_mode):
        reason = "solar" if has_solar_data else "solar_no_data"
    else:
        reason = "soc"
    return new_mode, reason
