"""Thin Home Assistant REST API client.

Used only for imperative actions the gateway needs to trigger directly —
currently just requesting a graceful shutdown on CRITICAL power mode.
Streaming state (power mode, solar projection) continues to flow over MQTT.
"""

from __future__ import annotations

import subprocess
import time

import httpx
import structlog

from gateway_node.config import settings

log = structlog.get_logger()


def request_shutdown() -> bool:
    """Stop HA gracefully, then halt the host. Returns True if the host halt
    command was issued (regardless of whether the HA stop call succeeded —
    the host must go down either way once CRITICAL is reached).

    POSTing homeassistant.stop alone previously only stopped the HA Core
    container, never the underlying OS — the Pico's hardware gate would then
    cut power to a fully-running Pi after its grace period, an unclean loss
    of power rather than the graceful halt this is meant to guarantee.
    """
    if settings.ha_token:
        url = f"{settings.ha_base_url.rstrip('/')}/api/services/homeassistant/stop"
        headers = {"Authorization": f"Bearer {settings.ha_token}"}
        try:
            resp = httpx.post(url, headers=headers, timeout=10)
            resp.raise_for_status()
            log.info("ha_stop_requested")
            time.sleep(5)  # give HA a moment to stop cleanly before the host goes down
        except Exception as e:  # noqa: BLE001
            log.warning("ha_stop_failed", error=str(e))
    else:
        log.warning("ha_stop_skipped", reason="ha_token not configured")

    try:
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=True, timeout=10)
        log.info("host_shutdown_requested")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("host_shutdown_failed", error=str(e))
        return False
