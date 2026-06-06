"""Thin Home Assistant REST API client.

Used only for imperative actions the gateway needs to trigger directly —
currently just requesting a graceful HA shutdown on CRITICAL power mode.
Streaming state (power mode, solar projection) continues to flow over MQTT.
"""

from __future__ import annotations

import httpx
import structlog

from gateway_node.config import settings

log = structlog.get_logger()


def request_shutdown() -> bool:
    """POST homeassistant.stop to HA. Returns True on success."""
    if not settings.ha_token:
        log.warning("ha_shutdown_skipped", reason="ha_token not configured")
        return False

    url = f"{settings.ha_base_url.rstrip('/')}/api/services/homeassistant/stop"
    headers = {"Authorization": f"Bearer {settings.ha_token}"}
    try:
        resp = httpx.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        log.info("ha_shutdown_requested")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("ha_shutdown_failed", error=str(e))
        return False
