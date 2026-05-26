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
│   └── <device-name>/        # Python packages, one per device type
├── infra/
│   └── tailscale/            # TailScale config / ACLs
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
- Node.js (version TBD) — for web app
- Python 3.x (version TBD) — for device packages
- TailScale installed and authenticated on dev machine

### Running the web app
```bash
# TBD once framework is chosen
```

### Working with a device package
```bash
cd packages/<device-name>
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Code Conventions

- Python: follow PEP 8; use `ruff` for linting and `black` for formatting
- TypeScript/JS: TBD once web stack is chosen
- Keep device packages independent — no cross-package imports
- All inter-device communication goes over TailScale; never assume LAN routing

## Environment Variables

Document env vars here as they are introduced. Example:
```
TAILSCALE_API_KEY=      # TailScale API key for device discovery
```
