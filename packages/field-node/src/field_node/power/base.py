from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PowerReading:
    voltage_v: float
    current_ma: float
    power_mw: float

    @property
    def is_discharging(self) -> bool:
        return self.current_ma < 0


class PowerMonitor(ABC):
    @abstractmethod
    def read(self) -> PowerReading:
        """Return the current voltage, current, and power from the battery."""
        ...

    @abstractmethod
    def close(self) -> None: ...
