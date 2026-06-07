"""SecurityMesh shared solar power-budget policy.

Pure, hardware-agnostic decision logic for the NORMAL → ECO → LOW → CRITICAL
power-saving state machine, shared by every node (Pi Zero field node, Pi 5
gateway). This package holds *decisions only* — it never touches hardware,
MQTT, the clock, or the filesystem. Each node owns:

  * its battery chemistry (voltage → SoC conversion), and
  * its actuation (what each mode actually stops/throttles).

and feeds this package the resulting ``soc_pct`` / current readings.
"""

from power_policy.policy import (
    DEFAULT_DAWN_LOW_SOC,
    DEFAULT_DAWN_RECOVERY_SOC,
    DEFAULT_DEFICIT_THRESHOLDS,
    DEFAULT_ENTER_AT,
    DEFAULT_EXIT_AT,
    PowerMode,
    SolarStatus,
    combine_modes,
    compute_dawn_recovery_mode,
    compute_night_mode,
    compute_solar_mode,
    evaluate_soc_mode,
    severity_index,
    stricter,
)

__all__ = [
    "DEFAULT_DAWN_LOW_SOC",
    "DEFAULT_DAWN_RECOVERY_SOC",
    "DEFAULT_DEFICIT_THRESHOLDS",
    "DEFAULT_ENTER_AT",
    "DEFAULT_EXIT_AT",
    "PowerMode",
    "SolarStatus",
    "combine_modes",
    "compute_dawn_recovery_mode",
    "compute_night_mode",
    "compute_solar_mode",
    "evaluate_soc_mode",
    "severity_index",
    "stricter",
]
