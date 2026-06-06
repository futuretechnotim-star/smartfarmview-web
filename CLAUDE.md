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
TAILSCALE_API_KEY=           # TailScale API key for device discovery
GATEWAY_NODE_HA_BASE_URL=    # HA REST base URL (default: http://localhost:8123)
GATEWAY_NODE_HA_TOKEN=       # HA long-lived access token (gateway node only)
```

---

## Claude Code + Codex Partnership

**Claude Code** is the deep implementation partner inside this repo — writing code,
running tests, editing configs, and committing changes.

**Codex** operates cross-repo: scouting requirements, reviewing diffs, planning
work across smartfarmview-web / landplan / HACS, and writing handoff briefs. Codex
maintains a coordination workbench at `/Users/timwebster/Documents/Codex/codex-workbench`.

When handing off to Codex, see **Handoff Format** below.

---

## Truth Sources

Primary documentation lives in `docs/` — treat it as the current source of
requirements. README files are useful for orientation but may lag active
development. When `docs/` and a README conflict, `docs/` wins.

Key docs:
- `docs/gateway-node.md` — power brain architecture, MQTT contract, hardware topology
- `docs/pico-watchdog.md` — Pico 2 W firmware, watchdog loop, power-cut thresholds
- `docs/SecurityMesh.md` — mesh overview, node roles, Tailscale ACL design
- `docs/setup-node.md` — repeatable field/gateway node provisioning procedure
- `docs/power-hat.md` — power hat hardware spec and wiring reference
- `docs/sensors.md` — sensor types, telemetry schema, field-node integration

---

## TDD and Test Expectations

Require tests for new behaviour where practical.

- **Write unit tests before implementation** for pure logic, policy engines, data
  transforms, and protocol encoders — this applies to `power-policy`, `field-node`
  power/telemetry logic, and `gateway-node` power brain and HA client.
- **Integration tests** around external seams: MQTT message handling, HA REST
  calls (use `httpx`'s mock transport), Pico UART protocol.
- Hardware-dependent code (camera, GPIO, UART) is exempt from unit tests but
  must have a `--dry-run` / mock path that *is* testable.

Test commands (run from each package directory with venv active):
```bash
pytest tests/unit/           # unit tests
ruff check src/ tests/       # lint
ruff format src/ tests/      # format
mypy src/                    # type check
```

CI runs all four and rejects the push on failure. Run locally first.

---

## Playwright / User Tests

This repo is not primarily a web UI repo — Playwright is not used here directly.

However, `apps/web` (when built) will expose the SmartFarmView dashboard and any
public-facing field-node status pages. When that work begins:
- Provide stable REST/WebSocket API contracts and mock data fixtures so Playwright
  tests in a separate test repo can drive the UI without needing live hardware.
- Keep API shape changes noted in PRs so cross-repo Playwright suites can be
  updated in tandem.

---

## GitHub Workflow Practice

- **Create an issue** for each coherent work slice before branching.
- **Branch per change** — `feat/`, `fix/`, `chore/` prefixes; keep branches short-lived.
- **Open draft PRs early** so Codex can review before the work is complete.
- PR descriptions must include:
  - What changed and why
  - Test plan (commands run, expected output)
  - Deployment notes (which nodes need re-deploy, env vars added)
  - Rollback notes for any destructive or irreversible steps
- Keep `smartfarmtechno.com/docs` impact in mind — if a change affects the
  documented setup procedure, update `docs/setup-node.md` in the same PR.

---

## Deployment Monitoring

After deploying to field or gateway nodes, confirm health via:

```bash
# Field node
systemctl status field-node --no-pager
journalctl -u field-node -n 20 --no-pager

# Gateway node
systemctl status gateway-power --no-pager
journalctl -u gateway-power -n 20 --no-pager
```

MQTT heartbeat (gateway publishes every 30 s):
```
topic: securitymesh/gateway/pi/heartbeat
```

Home Assistant discovery: **Settings → Devices & Services → MQTT** — node
devices should appear automatically on service start.

GitHub Actions deploy status:
```bash
gh run list --repo futuretechnotim-star/smartfarmview-web --limit 5
gh run watch <run-id>
```

CI rejects on: ruff lint errors, mypy type errors, failing unit tests.

---

## Handoff Format

Before passing work to Codex, write a brief at:
`/Users/timwebster/Documents/Codex/codex-workbench/handoffs/claude-to-codex.md`

Include:
- **Branch** and repo
- **Files changed** (with a one-line summary of each)
- **Tests run** and their results
- **Known risks** or fragile assumptions
- **Open questions** Codex should resolve or flag
