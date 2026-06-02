"""Tests for the gateway power brain.

The mode/hysteresis decisions are covered exhaustively in power-policy; here we
test the gateway-specific behaviour: that escalating modes stop the right
cumulative set of services and relaxing modes restart them, and that the SoC
state machine drives those actions end-to-end through a fake controller.
"""

from collections.abc import Iterator

import pytest
from power_policy import PowerMode

from gateway_node.config import settings
from gateway_node.power import GatewayPowerManager
from gateway_node.services import ServiceController


class FakeController(ServiceController):
    """Records start/stop calls instead of touching systemd/docker."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start(self, service: str) -> bool:
        self.started.append(service)
        return True

    def stop(self, service: str) -> bool:
        self.stopped.append(service)
        return True


@pytest.fixture
def controller() -> FakeController:
    return FakeController()


@pytest.fixture
def pm(
    controller: FakeController, monkeypatch: pytest.MonkeyPatch
) -> Iterator[GatewayPowerManager]:
    # Force nighttime to isolate SoC behaviour from solar projection (as field-node does).
    monkeypatch.setattr(GatewayPowerManager, "_is_daytime", lambda self: False)
    # Configure a representative per-mode service plan.
    monkeypatch.setattr(settings, "eco_stop", "frigate")
    monkeypatch.setattr(settings, "low_stop", "grafana")
    monkeypatch.setattr(settings, "critical_stop", "gateway-camera")
    yield GatewayPowerManager(controller=controller)


class TestModeTransitions:
    def test_starts_in_normal(self, pm: GatewayPowerManager) -> None:
        assert pm.mode == PowerMode.NORMAL

    def test_enters_eco_below_60(self, pm: GatewayPowerManager) -> None:
        pm.update(59)
        assert pm.mode == PowerMode.ECO

    def test_enters_critical_below_20(self, pm: GatewayPowerManager) -> None:
        pm.update(19)
        assert pm.mode == PowerMode.CRITICAL

    def test_camera_disabled_in_critical(self, pm: GatewayPowerManager) -> None:
        pm.update(19)
        assert pm.camera_enabled is False


class TestServiceActuation:
    def test_eco_stops_only_eco_service(
        self, pm: GatewayPowerManager, controller: FakeController
    ) -> None:
        pm.update(59)
        assert controller.stopped == ["frigate"]
        assert controller.started == []

    def test_critical_stops_cumulative_set(
        self, pm: GatewayPowerManager, controller: FakeController
    ) -> None:
        pm.update(10)  # jump straight to CRITICAL
        assert set(controller.stopped) == {"frigate", "grafana", "gateway-camera"}

    def test_escalation_only_stops_newly_added(
        self, pm: GatewayPowerManager, controller: FakeController
    ) -> None:
        pm.update(59)  # ECO → stop frigate
        controller.stopped.clear()
        pm.update(39)  # LOW → stop grafana only (frigate already stopped)
        assert controller.stopped == ["grafana"]

    def test_relaxing_restarts_services(
        self, pm: GatewayPowerManager, controller: FakeController
    ) -> None:
        pm.update(19)  # CRITICAL → all stopped
        controller.started.clear()
        controller.stopped.clear()
        pm.update(100)  # back to NORMAL → restart everything
        assert set(controller.started) == {"frigate", "grafana", "gateway-camera"}
        assert controller.stopped == []

    def test_no_action_when_mode_unchanged(
        self, pm: GatewayPowerManager, controller: FakeController
    ) -> None:
        pm.update(59)
        controller.stopped.clear()
        controller.started.clear()
        pm.update(58)  # still ECO
        assert controller.stopped == []
        assert controller.started == []
