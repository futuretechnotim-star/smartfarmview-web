# smartfarmview-gateway-node

The SecurityMesh **gateway** power-budget brain. Runs on the Raspberry Pi 5
gateway alongside Home Assistant, Mosquitto, BATMAN-adv mesh, the event-capture
camera, and the cellular/GNSS uplink (Waveshare SIM7600G-H DTU).

See [`docs/gateway-node.md`](../../docs/gateway-node.md) for the full hardware
architecture and [`docs/pico-watchdog.md`](../../docs/pico-watchdog.md) for the
power controller.

## What this package does

This is the **graceful** (software) tier of the two-tier power design:

1. Subscribes to battery/solar telemetry the **Pico 2 W watchdog** publishes
   over MQTT (the Pico reads the ECO-WORTHY charge controller over RS485).
2. Runs the shared `NORMAL → ECO → LOW → CRITICAL` policy
   (`smartfarmview-power-policy`) using the same SoC-hysteresis + solar
   end-of-day projection as the field node.
3. On each mode it stops a **cumulative** set of services to shed load, and
   restarts them when the mode relaxes. Services and backend are configured per
   deployment (`dry-run` by default — observe-only during the baseline phase).
4. Publishes the current mode/projection to MQTT for Home Assistant, and a
   heartbeat the Pico's **hardware** watchdog watches.

The Pico is the autonomous backstop: it enforces shutdown below the software
CRITICAL threshold and owns wake-on-recharge. This package never cuts power.

## Configuration

Environment variables (prefix `GATEWAY_NODE_`, or `/opt/gateway-node/.env`):

| Var | Default | Notes |
|---|---|---|
| `MQTT_HOST` / `MQTT_PORT` | `127.0.0.1` / `1883` | local broker |
| `PICO_TELEMETRY_TOPIC` | `securitymesh/gateway/pico/telemetry` | battery/solar in |
| `BATTERY_CAPACITY_MAH` | `20000` | 12.8V 20Ah LiFePO4 |
| `SOLAR_MIN_OVERNIGHT_SOC` | `40` | overnight reserve |
| `SERVICE_CONTROL` | `dry-run` | `dry-run` / `systemd` / `compose` |
| `ECO_STOP` / `LOW_STOP` / `CRITICAL_STOP` | `""` | comma-separated services per level |

## Dev

```bash
cd packages/gateway-node
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../power-policy
pip install -e ".[dev]"
ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/ && pytest tests/
```
