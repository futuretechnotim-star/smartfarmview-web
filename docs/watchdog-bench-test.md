# Pico-Watchdog Bench Test — Variable PSU Sweep

Procedure for validating (and eventually tuning) the pico-watchdog's power-gate
thresholds using a bench variable power supply in place of the real battery.
This turns hours of real charge/discharge cycling into a controlled, repeatable
voltage sweep — matches the bench test already called for in
[`gateway-node.md`](gateway-node.md) ("Bench, no solar: vary a simulated
battery voltage → confirm graceful `SHUTDOWN_REQ` → grace → cut, then recovery
re-power above the higher threshold").

## Why bench-test instead of real battery cycles

The state machine ([`gate_logic.py`](../packages/pico-watchdog/firmware/gate_logic.py))
is pure and already unit-tested under CPython for its *logic*. What a bench
sweep validates that unit tests can't:

- Real ADC/INA3221 voltage reading against a known, precise input
- Real timing of the grace period against how long an actual `sudo shutdown`
  takes with the full HA supervisor stack running
- Real hysteresis behavior under an actual battery-like load, not a mocked one
- Whether the configured thresholds (below) are actually the right numbers for
  this specific battery/load combination, or need retuning

## Equipment

- Variable bench PSU: 0–50V, 0–6A, 150W max (confirmed available as of
  2026-07-25)
- Pico 2 W watchdog, connected to a Mac (or other host) via USB for
  non-invasive monitoring (see **Monitoring**, below — do not use `mpremote`
  for anything other than the one post-test log pull)

## Current thresholds under test (`firmware/config.py`)

| Constant | Value | Meaning |
|---|---|---|
| `SHUTDOWN_VOLTAGE` | 12.0 V | Below this → request graceful shutdown |
| `RECOVERY_VOLTAGE` | 13.2 V | At/above this → safe to re-power (hysteresis gap above shutdown) |
| `GRACE_SECONDS` | 90 s | Max wait for the Pi to halt after `SHUTDOWN_REQ` before hard-cutting power |
| `HEARTBEAT_TIMEOUT_S` | 300 s | No Pi heartbeat this long while `PI_ON` → power-cycle (hung-Pi watchdog, not battery-related — see note below) |

These map onto the EcoWorthy 12.8V LiFePO4 curve in
[`soc.py`](../packages/pico-watchdog/firmware/soc.py) — flat through most of
its range (~13.0–13.3V), so the interesting behavior clusters near the edges.

**Note on `HEARTBEAT_TIMEOUT_S`:** during a bench sweep with the gateway
physically disconnected, no Pi is ever attached to send a heartbeat. Left
alone, the Pico will assume a (nonexistent) Pi has hung and power-cycle the
relay every 5 minutes regardless of voltage — this is expected firmware
behavior, not a bug, but it will pollute a sweep test's timing if not
accounted for. Either keep sweep segments under 5 minutes, or expect to see
`action=reboot` lines in the log unrelated to the voltage sweep and ignore
them.

## Safety setup

- [ ] Gateway physically disconnected — bench-testing the Pico/relay only
- [ ] PSU current limit set low (1–2A is generous headroom for the Pico +
      INA3221 + relay coils; protects against a wiring mistake)
- [ ] PSU output wired to the INA3221's battery channel (IN1+/IN1- tied
      together, rated to 26V — see wiring table in
      [`pico-watchdog.md`](pico-watchdog.md))
- [ ] **Double-check polarity before enabling PSU output**
- [ ] Confirm the Pico is running its normal loop (check via MQTT telemetry —
      `securitymesh/gateway/pico/telemetry` — not via `mpremote`, which
      interrupts the running loop; see **Monitoring** below)
- [ ] Start the PSU at a safe resting voltage (~13.6V) before enabling output

## Sweep procedure

Pause 30–60s at each step so the state machine settles and at least one
`LOOP_INTERVAL_S` (5s) sampling cycle passes.

**Downward sweep:**
- [ ] 13.6V → confirm `state=pi_on`, SoC ~100%
- [ ] 13.2V → confirm still `pi_on` (at `RECOVERY_VOLTAGE`, shouldn't matter
      going down — hysteresis only gates the *upward* transition)
- [ ] 12.8V → 12.5V → record SoC readings for comparison against `soc.py`'s
      curve
- [ ] 12.0V (`SHUTDOWN_VOLTAGE`) → confirm transition to `state=shutting_down`,
      `action=request_shutdown`
- [ ] Hold ~90–100s → confirm `action=cut_power` fires at/after
      `GRACE_SECONDS`
- [ ] 11.8V → 11.5V → confirm it stays `pi_off`, no flapping

**Upward sweep:**
- [ ] 11.5V → 12.0V → 12.8V → confirm still `pi_off` (below
      `RECOVERY_VOLTAGE`)
- [ ] 13.2V (`RECOVERY_VOLTAGE`) → confirm `action=restore_power`, transition
      to `pi_on`
- [ ] 13.6V → confirm stable

## Monitoring

Two options, in order of preference:

1. **MQTT telemetry** (non-invasive, safe to check anytime): subscribe to
   `securitymesh/gateway/pico/telemetry` on the gateway's broker. Published
   every `TELEMETRY_INTERVAL_S` (30s) with `gate_state`, `voltage_v`,
   `last_action`, `heartbeat_age_s`. Requires the Pico to be in WiFi range of
   the gateway's AP — if bench-testing away from the gateway, this won't be
   available and the local log (below) is the only option.
2. **Local log, after the fact** — `firmware/local_log.py`'s `RollingLog`
   writes every state transition/action plus a periodic snapshot to
   `watchdog.log` on the Pico's flash (bounded to ~32KB total, rotates
   automatically). Pull it once, after the sweep is done, via:
   ```
   mpremote connect /dev/cu.usbmodemXXXXX fs cat :watchdog.log
   ```
   **This is the only point in the whole procedure where touching the Pico
   over USB is appropriate** — `mpremote` interrupts the running loop to do
   filesystem operations, so pulling logs mid-sweep would corrupt the very
   test in progress (this happened once already during earlier debugging —
   see the pico-watchdog section of session notes / commit history around
   2026-07-24). If you need to check progress mid-sweep, use MQTT instead.

## After the sweep

- [ ] Pull `watchdog.log` (see above) and review the full timeline: every
      `action=` line should correspond to a real, deliberate voltage step
      from the sweep — not to unexplained gaps or unattributed transitions
- [ ] Compare logged `voltage_v` at each threshold crossing against the PSU's
      actual set point — any consistent offset points at an INA3221/ADC
      calibration issue, not a threshold-tuning issue
- [ ] If thresholds need adjusting based on findings, update
      `firmware/config.py` and this doc's threshold table together, and
      re-run the affected portion of the sweep to confirm

## Findings — 2026-07-28 bench session

**WiFi/MQTT clients must self-heal after an AP drop (field-reliability blocker).**
Repeatedly power-cycling the gateway during the sweep took down its WiFi AP
(`hostapd` on the Pi), and neither the Pico nor a far field node re-associated
on their own — both needed a *physical* power-cycle to come back. In the field
the AP drops on **every** low-voltage cut / OTA / gateway reboot, with nobody to
reset a remote node, so unattended recovery is mandatory. Root cause: a bare
`connect()`/reconnect retry can't clear a wedged radio (`wlan.isconnected()` can
even sit stale-`True` on a dead link), and there was no watchdog to reboot after
a long offline stretch.

- **Pico — fixed** (commit `c9fa454`): `_connect_wifi` now does a full radio
  bounce (`disconnect` → `active(False)` → `active(True)` → `connect`) before
  every attempt, plus a connectivity watchdog — `PI_ON` with no MQTT for
  `NET_RECOVERY_TIMEOUT_S` (900 s) → `machine.reset()` (safe: the NC relay keeps
  the Pi powered across the reset). Radio-bounce path is boot-validated;
  recovery-from-actual-AP-drop still wants a `hostapd`-restart bench test.
- **Field node (`packages/field-node`) — fixed** (`connectivity_watchdog.py`
  + wiring in `main.py`): confirmed live 2026-07-31 that the gap was real —
  after the same overnight incident that hung the gateway, LandPlanMesh1 did
  **not** re-associate on its own; it took a physical power-switch toggle to
  bring it back, exactly as predicted here. `main.py` now reboots
  (`sudo /usr/sbin/reboot`, already NOPASSWD via `pi-setup.sh`) if the broker
  has been unreachable for `connectivity_reboot_timeout_s` (900 s default,
  matching the Pico's `NET_RECOVERY_TIMEOUT_S`) while the service should be
  connected. `wpa_supplicant`'s own re-association behavior after an AP drop
  is still unverified on hardware — this is a backstop for if/when it
  doesn't, not a fix to `wpa_supplicant` itself.
- **Gateway (`packages/gateway-node` / `pico-watchdog`) — related gap found
  and fixed the same incident:** the gateway itself stayed powered but
  unreachable overnight and also needed a manual PSU restart. Root cause was
  adjacent but distinct from the field-node gap above — see
  `docs/pico-watchdog.md` "Sustained MQTT-loss recovery" for the fix (the
  Pico's own self-heal was resetting itself, not power-cycling the Pi). Also
  added from the same investigation: persistent/bounded gateway journald
  logging (`docs/gateway-node.md` "Logging" — there was no log history at all
  to diagnose the hang from), gateway CPU temperature telemetry, and WiFi
  RSSI telemetry on both the Pico and the field node.

**Threshold calibration:** the Pico's INA3221 reads ~0.02–0.05 V *below* the PSU
set point (graceful request fired at a 12.55 V PSU setting / 12.50 V logged).
Small, but account for it when reasoning about exact trip points — it's a
calibration offset, not a logic issue.

**Two-stage low-voltage cutoff validated:** the graceful request at 12.5 V gave
the Pi clean 5 V headroom to complete its ~25 s halt (heartbeat healthy at the
request), versus browning out ~55 s before the cut at the old single 12.0 V
threshold. The 12.0 V hard-cut is the backstop for a Pi that won't halt.
