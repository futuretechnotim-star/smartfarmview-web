import datetime
import subprocess
import time
from collections import deque

import structlog
from power_policy import (
    PowerMode,
    SolarStatus,
    combine_modes,
    compute_dawn_recovery_mode,
    compute_night_mode,
    compute_solar_mode,
    evaluate_soc_mode,
    severity_index,
)

from field_node.config import settings

log = structlog.get_logger()

# Re-exported for backwards compatibility: callers and tests import PowerMode /
# SolarStatus from this module. The decision logic now lives in the shared
# `power_policy` package; this module keeps the stateful + actuation concerns
# specific to a Pi Zero 2 W field node (rolling current window, clock, CPU
# governor, WiFi power-save).
__all__ = ["PowerManager", "PowerMode", "SolarStatus"]


class PowerManager:
    def __init__(self) -> None:
        self._mode = PowerMode.NORMAL
        self._soc_mode = PowerMode.NORMAL  # SoC component tracked separately for hysteresis
        self._current_history: deque[tuple[float, float]] = deque()  # (monotonic_time, current_ma)
        self._was_daytime = False
        self._solar_status = SolarStatus(is_daytime=False)
        self._dawn_soc_pct: int | None = None  # SoC recorded at most recent dawn transition

    @property
    def mode(self) -> PowerMode:
        return self._mode

    @property
    def solar_status(self) -> SolarStatus:
        return self._solar_status

    @property
    def motion_capture_enabled(self) -> bool:
        """True when PIR motion events should trigger a capture.

        False at night (no usable light) and in CRITICAL mode (battery conservation
        takes priority — periodic captures run instead via periodic_capture_interval_s).
        """
        if not self._is_daytime():
            return False
        return self._mode != PowerMode.CRITICAL

    @property
    def periodic_capture_interval_s(self) -> int | None:
        """Seconds between forced check-in captures in CRITICAL mode, or None.

        Returns None in all modes except CRITICAL (daytime), where motion capture
        is suppressed and a periodic image + detection keeps the node observable.
        """
        if self._mode == PowerMode.CRITICAL and self._is_daytime():
            return settings.critical_capture_interval_s
        return None

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

        # Dawn transition: snapshot SoC for recovery logic, clear overnight current history
        if is_day and not self._was_daytime:
            self._dawn_soc_pct = soc_pct
            self._current_history.clear()
            log.info(
                "solar_tracking_started",
                day_start=settings.solar_day_start_hour,
                dawn_soc_pct=soc_pct,
            )
        self._was_daytime = is_day

        if current_ma is not None and is_day:
            self._current_history.append((now, current_ma))

        # Drop readings older than the rolling window
        while self._current_history and now - self._current_history[0][0] > window:
            self._current_history.popleft()

        net_avg = self._net_avg_ma()

        # SoC mode: hysteresis tracked against _soc_mode, not the combined _mode
        new_soc_mode = evaluate_soc_mode(self._soc_mode, soc_pct)
        self._soc_mode = new_soc_mode

        # Solar mode: projection-based, no hysteresis (rolling avg provides smoothing)
        solar_mode, status = compute_solar_mode(
            is_daytime=is_day,
            soc_pct=soc_pct,
            net_avg_ma=net_avg,
            hours_until_eod=self._hours_until_eod(),
            battery_capacity_mah=settings.battery_capacity_mah,
            min_overnight_soc=settings.solar_min_overnight_soc,
        )
        self._solar_status = status

        new_mode, reason = combine_modes(
            new_soc_mode, solar_mode, has_solar_data=net_avg is not None
        )

        # Night floor: enforce LOW during dark hours (camera useless, conserve battery)
        night_mode = compute_night_mode(is_daytime=is_day)
        if night_mode is not None and severity_index(night_mode) > severity_index(new_mode):
            new_mode = night_mode
            reason = "night"

        # Dawn recovery: if the battery woke depleted, hold LOW until recharged
        dawn_mode = compute_dawn_recovery_mode(
            dawn_soc_pct=self._dawn_soc_pct,
            current_soc_pct=soc_pct,
            dawn_low_threshold=settings.dawn_low_soc_threshold,
            recovery_threshold=settings.dawn_recovery_soc,
        )
        if dawn_mode is not None and severity_index(dawn_mode) > severity_index(new_mode):
            new_mode = dawn_mode
            reason = "dawn_recovery"

        status.mode_reason = reason

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

    def _hours_until_eod(self) -> float:
        now_dt = datetime.datetime.now()
        return max(0.0, settings.solar_day_end_hour - now_dt.hour - now_dt.minute / 60.0)

    def _net_avg_ma(self) -> float | None:
        if len(self._current_history) < 2:
            return None
        return sum(c for _, c in self._current_history) / len(self._current_history)


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
