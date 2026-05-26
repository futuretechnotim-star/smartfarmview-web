from abc import ABC, abstractmethod
from dataclasses import dataclass

# Piecewise linear approximation of Li-ion discharge curve (voltage → SoC %).
# Points ordered high-to-low so the first pair above the reading can be found with next().
_SOC_CURVE: list[tuple[float, int]] = [
    (4.20, 100),
    (4.15, 95),
    (4.11, 90),
    (4.08, 85),
    (4.02, 80),
    (3.98, 75),
    (3.95, 70),
    (3.91, 65),
    (3.87, 60),
    (3.83, 55),
    (3.79, 50),
    (3.75, 45),
    (3.71, 40),
    (3.67, 35),
    (3.63, 30),
    (3.59, 25),
    (3.55, 20),
    (3.51, 15),
    (3.45, 10),
    (3.40, 5),
    (3.30, 0),
]


def _voltage_to_soc(voltage_v: float) -> int:
    if voltage_v >= _SOC_CURVE[0][0]:
        return 100
    if voltage_v <= _SOC_CURVE[-1][0]:
        return 0
    for i in range(len(_SOC_CURVE) - 1):
        v_hi, soc_hi = _SOC_CURVE[i]
        v_lo, soc_lo = _SOC_CURVE[i + 1]
        if v_lo <= voltage_v <= v_hi:
            ratio = (voltage_v - v_lo) / (v_hi - v_lo)
            return round(soc_lo + ratio * (soc_hi - soc_lo))
    return 0


@dataclass
class PowerReading:
    voltage_v: float
    current_ma: float
    power_mw: float

    @property
    def is_discharging(self) -> bool:
        return self.current_ma < 0

    @property
    def soc_pct(self) -> int:
        return _voltage_to_soc(self.voltage_v)

    def runtime_hours(self, capacity_mah: int) -> float | None:
        """Estimated hours of runtime remaining based on current draw. None if not discharging."""
        if not self.is_discharging or abs(self.current_ma) < 1.0:
            return None
        remaining_mah = capacity_mah * (self.soc_pct / 100)
        return round(remaining_mah / abs(self.current_ma), 1)


class PowerMonitor(ABC):
    @abstractmethod
    def read(self) -> PowerReading:
        """Return the current voltage, current, and power from the battery."""
        ...

    @abstractmethod
    def close(self) -> None: ...
