import datetime
import enum
import subprocess
import time
from collections import deque
from dataclasses import dataclass

import structlog

from field_node.config import settings

log = structlog.get_logger()


class PowerMode(enum.Enum):
    NORMAL = "normal"  # full operation
    ECO = "eco"  # WiFi PSM + CPU powersave + camera standby between captures
    LOW = "low"  # + slow telemetry (5 min floor)
    CRITICAL = "critical"  # + camera disabled, minimal telemetry (10 min floor)


_SEVERITY = [PowerMode.NORMAL, PowerMode.ECO, PowerMode.LOW, PowerMode.CRITICAL]

# SoC % thresholds to enter / leave each mode (5 % hysteresis prevents flapping)
_ENTER_AT: dict[PowerMode, int] = {
    PowerMode.CRITICAL: 20,
    PowerMode.LOW: 40,
    PowerMode.ECO: 60,
}
_EXIT_AT: dict[PowerMode, int] = {
    PowerMode.CRITICAL: 25,
    PowerMode.LOW: 45,
    PowerMode.ECO: 65,
}

# Projected end-of-day SoC deficit → solar-driven mode escalation
# (deficit = solar_min_overnight_soc - projected_eod_soc)
_DEFICIT_THRESHOLDS: list[tuple[float, PowerMode]] = [
    (25.0, PowerMode.CRITICAL),
    (10.0, PowerMode.LOW),
    (0.0, PowerMode.ECO),
]


@dataclass
class SolarStatus:
    is_daytime: bool
    net_avg_ma: float | None = None  # rolling avg of battery_current_ma (+ = charging)
    projected_eod_soc: float | None = None  # estimated SoC % at end of solar day
    deficit_pct: float | None = None  # min_overnight_soc - projected_eod_soc (>0 = shortfall)
    mode_reason: str = "soc"  # "soc" | "solar" | "solar_no_data"


def _severity_idx(mode: PowerMode) -> int:
    return _SEVERITY.index(mode)


def _stricter(a: PowerMode, b: PowerMode | None) -> PowerMode:
    if b is None:
        return a
    return _SEVERITY[max(_severity_idx(a), _severity_idx(b))]


class PowerManager:
    def __init__(self) -> None:
        self._mode = PowerMode.NORMAL
        self._soc_mode = PowerMode.NORMAL  # SoC component tracked separately for hysteresis
        self._current_history: deque[tuple[float, float]] = deque()  # (monotonic_time, current_ma)
        self._was_daytime = False
        self._solar_status = SolarStatus(is_daytime=False)

    @property
    def mode(self) -> PowerMode:
        return self._mode

    @property
    def solar_status(self) -> SolarStatus:
        return self._solar_status

    @property
    def camera_enabled(self) -> bool:
        return self._mode != PowerMode.CRITICAL

    @property
    def camera_standby_between_captures(self) -> bool:
        # ECO and above: park camera between captures to save ~100 mA
        return self._mode in (PowerMode.ECO, PowerMode.LOW)

    @property
    def telemetry_interval_seconds(self) -> int:
        floors = {PowerMode.LOW: 300, PowerMode.CRITICAL: 600}
        return max(settings.telemetry_interval_seconds, floors.get(self._mode, 0))

    def update(self, soc_pct: int, current_ma: float | None = None) -> PowerMode:
        now = time.monotonic()
        window = settings.solar_current_avg_minutes * 60.0
        is_day = self._is_daytime()

        # Clear stale overnight readings on dawn transition so they don't skew daytime average
        if is_day and not self._was_daytime:
            self._current_history.clear()
            log.info("solar_tracking_started", day_start=settings.solar_day_start_hour)
        self._was_daytime = is_day

        if current_ma is not None and is_day:
            self._current_history.append((now, current_ma))

        # Drop readings older than the rolling window
        while self._current_history and now - self._current_history[0][0] > window:
            self._current_history.popleft()

        # SoC mode: hysteresis tracked against _soc_mode, not the combined _mode
        new_soc_mode = self._evaluate_soc(soc_pct)
        self._soc_mode = new_soc_mode

        # Solar mode: projection-based, no hysteresis (rolling avg provides smoothing)
        solar_mode, status = self._compute_solar_mode(soc_pct)
        self._solar_status = status

        new_mode = _stricter(new_soc_mode, solar_mode)

        if solar_mode is not None and _severity_idx(solar_mode) > _severity_idx(new_soc_mode):
            status.mode_reason = "solar_no_data" if self._net_avg_ma() is None else "solar"
        else:
            status.mode_reason = "soc"

        if new_mode != self._mode:
            log.info(
                "power_mode_change",
                from_mode=self._mode.value,
                to_mode=new_mode.value,
                soc_pct=soc_pct,
                reason=status.mode_reason,
                net_avg_ma=status.net_avg_ma,
                deficit_pct=status.deficit_pct,
            )
            _apply_mode(new_mode)
            self._mode = new_mode

        return self._mode

    def _is_daytime(self) -> bool:
        hour = datetime.datetime.now().hour
        return settings.solar_day_start_hour <= hour < settings.solar_day_end_hour

    def _net_avg_ma(self) -> float | None:
        if len(self._current_history) < 2:
            return None
        return sum(c for _, c in self._current_history) / len(self._current_history)

    def _evaluate_soc(self, soc_pct: int) -> PowerMode:
        for mode in (PowerMode.CRITICAL, PowerMode.LOW, PowerMode.ECO):
            if self._soc_mode == mode:
                if soc_pct < _EXIT_AT[mode]:
                    return mode
            elif soc_pct < _ENTER_AT[mode]:
                return mode
        return PowerMode.NORMAL

    def _compute_solar_mode(self, soc_pct: int) -> tuple[PowerMode | None, SolarStatus]:
        is_day = self._is_daytime()
        net_avg = self._net_avg_ma()

        if not is_day:
            return None, SolarStatus(is_daytime=False, net_avg_ma=net_avg)

        if net_avg is None:
            # Daytime but rolling window not yet populated — be mildly conservative
            return PowerMode.ECO, SolarStatus(is_daytime=True, mode_reason="solar_no_data")

        now_dt = datetime.datetime.now()
        hours_until_eod = max(
            0.0,
            settings.solar_day_end_hour - now_dt.hour - now_dt.minute / 60.0,
        )
        remaining_mah = settings.battery_capacity_mah * (soc_pct / 100.0)
        projected_mah = min(
            float(settings.battery_capacity_mah),
            remaining_mah + net_avg * hours_until_eod,
        )
        projected_soc = projected_mah / settings.battery_capacity_mah * 100.0
        deficit = settings.solar_min_overnight_soc - projected_soc

        solar_mode: PowerMode | None = None
        for threshold, candidate in _DEFICIT_THRESHOLDS:
            if deficit > threshold:
                solar_mode = candidate
                break

        return solar_mode, SolarStatus(
            is_daytime=True,
            net_avg_ma=round(net_avg, 1),
            projected_eod_soc=round(projected_soc, 1),
            deficit_pct=round(deficit, 1),
        )


def _apply_mode(mode: PowerMode) -> None:
    governor = "ondemand" if mode == PowerMode.NORMAL else "powersave"
    _set_cpu_governor(governor)
    _set_wifi_psm(enabled=mode != PowerMode.NORMAL)


def _set_cpu_governor(governor: str) -> None:
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
