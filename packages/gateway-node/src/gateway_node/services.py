"""Service actuation for the gateway power brain.

Stops/starts services as the power mode escalates/relaxes. Three backends:

- ``systemd``  — ``systemctl start/stop <unit>``
- ``compose``  — ``docker compose -f <file> start/stop <service>``
- ``dry-run``  — log the intended action only (safe default during the baseline
  measurement phase, before the operator opts services into throttling)

The controller never raises on a failed action — power management must keep
running even if one unit is misconfigured; failures are logged and reported.
"""

from __future__ import annotations

import subprocess

import structlog

from gateway_node.config import settings

log = structlog.get_logger()


class ServiceController:
    def __init__(self, mode: str | None = None, compose_file: str | None = None) -> None:
        self._mode = mode if mode is not None else settings.service_control
        self._compose_file = compose_file if compose_file is not None else settings.compose_file

    def start(self, service: str) -> bool:
        return self._run("start", service)

    def stop(self, service: str) -> bool:
        return self._run("stop", service)

    def _run(self, action: str, service: str) -> bool:
        if self._mode == "dry-run":
            log.info("service_action_dry_run", action=action, service=service)
            return True

        if self._mode == "systemd":
            cmd = ["sudo", "systemctl", action, service]
        elif self._mode == "compose":
            cmd = ["docker", "compose", "-f", self._compose_file, action, service]
        else:
            log.warning("service_control_unknown", mode=self._mode)
            return False

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:  # noqa: BLE001 — never let actuation crash the brain
            log.warning("service_action_error", action=action, service=service, error=str(e))
            return False

        if result.returncode != 0:
            log.warning(
                "service_action_failed",
                action=action,
                service=service,
                stderr=result.stderr.strip(),
            )
            return False

        log.info("service_action", action=action, service=service)
        return True
