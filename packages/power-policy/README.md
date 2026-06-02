# smartfarmview-power-policy

Shared, **pure** decision logic for the SecurityMesh solar power budget — the
`NORMAL → ECO → LOW → CRITICAL` state machine, SoC hysteresis, and solar
end-of-day projection. Imported by both `field-node` (Pi Zero 2 W) and
`gateway-node` (Pi 5) so there is a single source of truth with no drift.

## Why a separate package?

`CLAUDE.md` requires device packages to stay independent (no cross-package
imports). This is **not** a device package — it is a hardware-agnostic library
with **zero runtime dependencies** (stdlib only). Both device packages depend on
it; neither depends on the other.

## Scope

This package decides *modes only*. It deliberately does **not** know about:

- **battery chemistry** — voltage→SoC conversion stays in each node (Li-ion
  curve on field nodes, LiFePO4 / charge-controller-reported SoC on the gateway);
- **actuation** — what each mode stops/throttles is node-specific
  (`_apply_mode` lives in each node's `PowerManager`);
- **I/O** — no MQTT, clock, sysfs, or filesystem access.

Callers pass in `soc_pct`, a rolling net-current average, and the time-of-day
context, and get back a recommended `PowerMode`.

## Public API

```python
from power_policy import (
    PowerMode,            # NORMAL / ECO / LOW / CRITICAL
    SolarStatus,          # telemetry dataclass
    evaluate_soc_mode,    # SoC-driven mode with hysteresis
    compute_solar_mode,   # projected-EoD-SoC-driven escalation
    combine_modes,        # merge SoC + solar → effective mode + reason
    stricter, severity_index,
)
```

Default thresholds (`DEFAULT_ENTER_AT`, `DEFAULT_EXIT_AT`,
`DEFAULT_DEFICIT_THRESHOLDS`) are exported and can be overridden per node by
passing custom dicts.

## Dev

```bash
cd packages/power-policy
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src/ tests/ && ruff format --check src/ tests/
mypy src/
pytest tests/
```
