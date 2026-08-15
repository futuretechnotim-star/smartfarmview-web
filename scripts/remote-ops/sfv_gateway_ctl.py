#!/usr/bin/env python3
"""Remote troubleshooting CLI for the SFV gateway — talks to Mosquitto
directly over Tailscale, no SSH session required.

Usage:
    python3 sfv_gateway_ctl.py status
    python3 sfv_gateway_ctl.py reboot-gateway

Connection settings come from env vars (all optional):
    SFV_MQTT_HOST      default: 100.127.225.15 (sfv-gateway's Tailscale IP)
    SFV_MQTT_PORT      default: 1883
    SFV_MQTT_USERNAME  default: "" (anonymous)
    SFV_MQTT_PASSWORD  default: ""
    SFV_NODE_ID        default: sfv-gateway (gateway's own node_id/hostname)

Requires: pip install -r requirements.txt (paho-mqtt)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

DEFAULT_HOST = os.environ.get("SFV_MQTT_HOST", "100.127.225.15")
DEFAULT_PORT = int(os.environ.get("SFV_MQTT_PORT", "1883"))
DEFAULT_USERNAME = os.environ.get("SFV_MQTT_USERNAME", "")
DEFAULT_PASSWORD = os.environ.get("SFV_MQTT_PASSWORD", "")
NODE_ID = os.environ.get("SFV_NODE_ID", "sfv-gateway")

PICO_TELEMETRY_TOPIC = "securitymesh/gateway/pico/telemetry"
PICO_CMD_TOPIC = "securitymesh/gateway/pico/cmd"
GATEWAY_POWER_TOPIC = f"securitymesh/{NODE_ID}/power"
GATEWAY_STATUS_TOPICS = [
    f"securitymesh/{NODE_ID}/camera/status",
    f"securitymesh/{NODE_ID}/nodes/online",
]


@dataclass
class _Collected:
    messages: dict[str, dict] = field(default_factory=dict)


def _build_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sfv-remote-ctl-{os.getpid()}")
    if DEFAULT_USERNAME:
        client.username_pw_set(DEFAULT_USERNAME, DEFAULT_PASSWORD)
    return client


def _collect(topics: list[str], timeout_s: float) -> dict[str, dict]:
    """Subscribe to `topics` (retained + live) and collect whatever arrives
    within timeout_s. Good enough for a status snapshot — most of these
    topics are retained, so the broker replies almost immediately."""
    collected = _Collected()

    def on_connect(c, userdata, flags, rc, props=None):
        for t in topics:
            c.subscribe(t)

    def on_message(c, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            payload = {"_raw": msg.payload.decode(errors="replace")}
        collected.messages[msg.topic] = payload

    client = _build_client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(DEFAULT_HOST, DEFAULT_PORT, keepalive=30)
    client.loop_start()
    time.sleep(timeout_s)
    client.loop_stop()
    client.disconnect()
    return collected.messages


def cmd_status(args: argparse.Namespace) -> int:
    print(f"Connecting to {DEFAULT_HOST}:{DEFAULT_PORT} ...")
    # Power/node-count topics are retained (arrive instantly); pico telemetry
    # is NOT retained (mqtt_link.py publish_telemetry has no retain=True) and
    # only publishes every 30s, so waiting long enough to catch one cycle is
    # the only way to reliably see it here rather than missing it at random.
    print("(waiting up to 32s to catch a Pico telemetry cycle — it isn't retained)")
    data = _collect([PICO_TELEMETRY_TOPIC, GATEWAY_POWER_TOPIC, *GATEWAY_STATUS_TOPICS], timeout_s=32.0)

    pico = data.get(PICO_TELEMETRY_TOPIC)
    print("\n--- Pico watchdog ---")
    if pico:
        for key in (
            "gate_state",
            "voltage_v",
            "soc_pct",
            "heartbeat_age_s",
            "halt_confirmed",
            "enclosure_temp_c",
            "enclosure_humidity_pct",
            "fan_on",
            "wifi_rssi_dbm",
        ):
            if key in pico:
                print(f"  {key}: {pico[key]}")
    else:
        print("  no telemetry received (Pico offline or unreachable)")

    power = data.get(GATEWAY_POWER_TOPIC)
    print("\n--- Gateway power brain ---")
    if power:
        for key in ("mode", "camera_enabled", "is_daytime", "net_avg_ma", "projected_eod_soc", "deficit_pct"):
            if key in power:
                print(f"  {key}: {power[key]}")
    else:
        print("  no power state received (gateway-power offline or unreachable)")

    nodes = data.get(f"securitymesh/{NODE_ID}/nodes/online")
    if nodes:
        print(f"\n--- Field nodes online: {nodes} ---")

    if not any([pico, power]):
        print("\nNothing came back at all — check SFV_MQTT_HOST/credentials, or the gateway may be down.")
        return 1
    return 0


def cmd_reboot_gateway(args: argparse.Namespace) -> int:
    if not args.yes:
        confirm = input(
            "This will gracefully halt the gateway Pi and power-cycle it via the "
            "Pico watchdog relay. Continue? [y/N] "
        )
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return 1

    print(f"Publishing reboot_gateway command to {PICO_CMD_TOPIC} ...")
    client = _build_client()
    client.connect(DEFAULT_HOST, DEFAULT_PORT, keepalive=30)
    client.loop_start()
    client.publish(PICO_CMD_TOPIC, json.dumps({"cmd": "reboot_gateway"}), qos=1)
    time.sleep(0.5)  # let the publish actually flush before we disconnect

    print("Command sent. Watching Pico telemetry for progress "
          f"(up to {args.timeout}s) ...")

    seen_statuses: set[str] = set()

    def on_message(c, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            return
        status = payload.get("reboot_status")
        if status and status not in seen_statuses:
            seen_statuses.add(status)
            print(f"  [{time.strftime('%H:%M:%S')}] reboot_status: {status}")

    client.on_message = on_message
    client.subscribe(PICO_TELEMETRY_TOPIC)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        client.loop(timeout=1.0)
        if "halt_confirmed" in seen_statuses or "halt_timed_out_pulsing_anyway" in seen_statuses:
            print("\nRelay pulsed — gateway is power-cycling now.")
            print("Give it 1-2 minutes to boot, then re-run `status` to confirm it's back.")
            break
    else:
        print(
            "\nNo reboot_status update seen within the timeout. Either the Pico "
            "didn't receive the command (check MQTT connectivity / Pico is online) "
            "or it's mid-wait for halt_confirmed — check again shortly."
        )

    client.loop_stop()
    client.disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Snapshot of Pico + gateway-power state over MQTT")
    p_status.set_defaults(func=cmd_status)

    p_reboot = sub.add_parser(
        "reboot-gateway",
        help="Graceful halt + PSU power-cycle of the gateway Pi via the Pico watchdog",
    )
    p_reboot.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_reboot.add_argument("--timeout", type=float, default=150.0, help="seconds to wait for a reboot_status update (default 150 — covers the 120s halt-wait plus margin)")
    p_reboot.set_defaults(func=cmd_reboot_gateway)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
