"""Gateway power-budget brain.

Same NORMAL → ECO → LOW → CRITICAL state machine as the field node (decision
logic shared via ``power_policy``), but the **actuation** is gateway-specific:
instead of tweaking a Pi Zero's CPU governor / WiFi power-save, each mode
stops a cumulative set of services to shed load, and relaxing the mode restarts
them. The Pico 2 W watchdog is the independent hardware backstop below CRITICAL.
"""

from __future__ import annotations

import datetime
import time
from collections import deque

import structlog
from power_policy import (
    PowerMode,
    SolarStatus,
    combine_modes,
    compute_solar_mode,
    evaluate_soc_mode,
    severity_index,
)

from gateway_node.config import settings
from gateway_node.services import ServiceController

log = structlog.get_logger()


class GatewayPowerManager:
    def __init__(self, controller: ServiceController | None = None) -> None:
        self._mode = PowerMode.NORMAL
        self._soc_mode = PowerMode.NORMAL  # SoC component tracked separately for hysteresis
        self._current_history: deque[tuple[float, float]] = deque()  # (monotonic_time, current_ma)
        self._was_daytime = False
        self._solar_status = SolarStatus(is_daytime=False)
        self._controller = controller if controller is not None else ServiceController()
        self._stopped: set[str] = set()  # services currently stopped by the brain

    @property
    def mode(self) -> PowerMode:
        return self._mode

    @property
    def solar_status(self) -> SolarStatus:
        return self._solar_status

    @property
    def camera_enabled(self) -> bool:
        return self._mode != PowerMode.CRITICAL

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

        while self._current_history and now - self._current_history[0][0] > window:
            self._current_history.popleft()

        net_avg = self._net_avg_ma()

        new_soc_mode = evaluate_soc_mode(self._soc_mode, soc_pct)
        self._soc_mode = new_soc_mode

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
            self._apply_mode(new_mode)
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

    def _desired_stopped(self, mode: PowerMode) -> set[str]:
        """Cumulative set of services that should be stopped at ``mode`` — each
        level adds to the levels below it (ECO ⊆ LOW ⊆ CRITICAL)."""
        stopped: set[str] = set()
        if severity_index(mode) >= severity_index(PowerMode.ECO):
            stopped.update(settings.stop_list("eco"))
        if severity_index(mode) >= severity_index(PowerMode.LOW):
            stopped.update(settings.stop_list("low"))
        if severity_index(mode) >= severity_index(PowerMode.CRITICAL):
            stopped.update(settings.stop_list("critical"))
        return stopped

    def _apply_mode(self, mode: PowerMode) -> None:
        desired = self._desired_stopped(mode)
        to_start = self._stopped - desired  # mode relaxed → bring these back
        to_stop = desired - self._stopped  # mode escalated → shed these

        for service in sorted(to_start):
            self._controller.start(service)
        for service in sorted(to_stop):
            self._controller.stop(service)

        self._stopped = desired
