import enum
import subprocess

import structlog

from field_node.config import settings

log = structlog.get_logger()


class PowerMode(enum.Enum):
    NORMAL = "normal"  # SoC >= 60 %: full operation
    ECO = "eco"  # 40–60 %: WiFi PSM + CPU powersave
    LOW = "low"  # 20–40 %: + camera standby, slow telemetry
    CRITICAL = "critical"  # < 20 %: + camera disabled, minimal telemetry


# SoC % threshold to enter each mode (decreasing battery)
_ENTER_AT: dict[PowerMode, int] = {
    PowerMode.CRITICAL: 20,
    PowerMode.LOW: 40,
    PowerMode.ECO: 60,
}
# Hysteresis: SoC % must rise above this to leave a mode (prevents rapid flapping)
_EXIT_AT: dict[PowerMode, int] = {
    PowerMode.CRITICAL: 25,
    PowerMode.LOW: 45,
    PowerMode.ECO: 65,
}


class PowerManager:
    def __init__(self) -> None:
        self._mode = PowerMode.NORMAL

    @property
    def mode(self) -> PowerMode:
        return self._mode

    @property
    def camera_enabled(self) -> bool:
        return self._mode != PowerMode.CRITICAL

    @property
    def camera_standby_between_captures(self) -> bool:
        return self._mode == PowerMode.LOW

    @property
    def telemetry_interval_seconds(self) -> int:
        floors = {PowerMode.LOW: 300, PowerMode.CRITICAL: 600}
        return max(settings.telemetry_interval_seconds, floors.get(self._mode, 0))

    def update(self, soc_pct: int) -> PowerMode:
        """Evaluate SoC, transition mode if needed, apply system changes. Returns current mode."""
        new_mode = self._evaluate(soc_pct)
        if new_mode != self._mode:
            log.info(
                "power_mode_change",
                from_mode=self._mode.value,
                to_mode=new_mode.value,
                soc_pct=soc_pct,
            )
            _apply_mode(new_mode)
            self._mode = new_mode
        return self._mode

    def _evaluate(self, soc_pct: int) -> PowerMode:
        # Check most severe first; hysteresis prevents leaving a mode until SoC recovers
        for mode in (PowerMode.CRITICAL, PowerMode.LOW, PowerMode.ECO):
            if self._mode == mode:
                if soc_pct < _EXIT_AT[mode]:
                    return mode
            elif soc_pct < _ENTER_AT[mode]:
                return mode
        return PowerMode.NORMAL


def _apply_mode(mode: PowerMode) -> None:
    governor = "ondemand" if mode == PowerMode.NORMAL else "powersave"
    _set_cpu_governor(governor)
    _set_wifi_psm(enabled=mode != PowerMode.NORMAL)


def _set_cpu_governor(governor: str) -> None:
    # Pi Zero 2 W has 4 cores; write via sudo tee since sysfs is root-owned
    for i in range(4):
        path = f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor"
        try:
            result = subprocess.run(
                ["sudo", "tee", path],
                input=governor,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                log.warning("cpu_governor_write_failed", cpu=i, stderr=result.stderr.strip())
                return
        except Exception as e:
            log.warning("cpu_governor_error", cpu=i, error=str(e))
            return
    log.info("cpu_governor_set", governor=governor)


def _set_wifi_psm(*, enabled: bool) -> None:
    # PSM reduces WiFi idle current ~50–80 mA; may add latency — disabled in NORMAL mode
    try:
        result = subprocess.run(
            ["sudo", "iwconfig", "wlan0", "power", "on" if enabled else "off"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            log.warning("wifi_psm_failed", stderr=result.stderr.strip())
        else:
            log.info("wifi_psm_set", enabled=enabled)
    except Exception as e:
        log.warning("wifi_psm_error", error=str(e))
