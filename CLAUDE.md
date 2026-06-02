# SmartFarmView Web — Monorepo

## Overview

Monorepo for the SmartFarmView platform. Contains a web frontend and multiple Python device packages that communicate over a TailScale mesh network.

**GitHub:** https://github.com/futuretechnotim-star/smartfarmview-web

## Repository Structure

```
smartfarmview-web/
├── apps/
│   └── web/                  # Web frontend (TBD: framework)
├── packages/
│   ├── power-policy/         # Shared pure power-budget policy lib (no hardware deps)
│   ├── field-node/           # Pi Zero 2 W field node (camera, motion, telemetry)
│   ├── gateway-node/         # Pi 5 gateway power brain (HA/MQTT/mesh host)
│   └── pico-watchdog/        # Pico 2 W power controller firmware (MicroPython)
├── infra/
│   └── nodes.json            # Node registry (field-node deploy matrix filters by `type`)
├── docs/                     # gateway-node.md, pico-watchdog.md, SecurityMesh.md, …
├── CLAUDE.md
└── README.md
```

> Structure is provisional — update this file as packages and apps are added.

## Apps

### `apps/web`
- Web frontend for the SmartFarmView dashboard
- Stack TBD

## Python Device Packages

Each device type lives in `packages/<device-name>/` as a standalone Python package with its own `pyproject.toml`.

Conventions:
- Package name: `smartfarmview-<device-name>` (e.g. `smartfarmview-sensor`)
- Minimum Python version: TBD
- Use `pyproject.toml` (not `setup.py`)
- Each package has its own `README.md`, `tests/`, and virtual environment

## TailScale Integration

Devices communicate over a TailScale mesh. Key points:
- Each device node has a stable TailScale hostname
- Device packages authenticate and discover peers via the TailScale API / MagicDNS
- ACL config lives in `infra/tailscale/`

## Development

### Prerequisites
- Python 3.13 — for device packages (see `.python-version`)
- Node.js (version TBD) — for web app
- TailScale installed and authenticated on dev machine

### Local dev setup for a device package
```bash
cd packages/<device-name>
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Before pushing — always run locally
```bash
ruff check src/ tests/     # lint
ruff format src/ tests/    # format (auto-fixes in place)
mypy src/                  # type check
pytest tests/unit/         # unit tests
```

CI runs all four steps and will reject the push if any fail. Run them locally to avoid the round-trip.

## Code Conventions

- Python: follow PEP 8; use `ruff` for linting and `black` for formatting
- TypeScript/JS: TBD once web stack is chosen
- Keep device packages independent — no imports between device packages. Shared
  logic goes in a dedicated hardware-agnostic library package instead:
  `packages/power-policy/` (`smartfarmview-power-policy`) holds the power-budget
  state machine imported by both `field-node` and `gateway-node`. CI/deploy
  install such libs editable first (`pip install -e ../power-policy`) so the
  dependency resolves locally (they are not published to PyPI).
- All inter-device communication goes over TailScale; never assume LAN routing

## Environment Variables

Document env vars here as they are introduced. Example:
```
TAILSCALE_API_KEY=      # TailScale API key for device discovery
```
