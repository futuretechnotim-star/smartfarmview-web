# Gateway Node — Architecture & Power Budget

The gateway is the SecurityMesh central infrastructure node: Home Assistant,
Mosquitto MQTT broker, BATMAN-adv mesh coordination, an event-capture camera,
and the internet uplink. It runs **off-grid and internet-independent**,
unattended most of the time, on a fixed daily solar energy budget.

> Status: **baseline build**. The hardware below is intentionally the same
> modest kit as a field node so we can measure real consumption, validate the
> watchdog, and size the production system from data (see
> [Power budget](#power-budget--predictions-to-validate)).

## The core problem

A Raspberry Pi **cannot wake itself after a deep-battery shutdown**. The Pi 5's
RTC/PMIC only wakes from a low-power *standby* that still needs the 5V rail
energised (~3mA), and a timed wake has no idea whether the battery actually
recovered. A solar charge controller's own low-voltage disconnect yanks the load
hard, risking SD/NVMe corruption. So power management is **two tiers**:

| Tier | Where | Does |
|---|---|---|
| **Graceful (software)** | `gateway-node` + Home Assistant | Proactively throttles/stops services as SoC + solar projection worsen (`NORMAL → ECO → LOW → CRITICAL`), then requests a clean shutdown. |
| **Enforcing (hardware)** | Pico 2 W watchdog | Cuts the Pi's 5V if voltage hits the safety floor, **re-powers on recharge**, and power-cycles a hung Pi. Fully autonomous — no network needed. |

The gate's hard-cutoff voltage sits **below** the software CRITICAL threshold so
HA always gets first chance to act gracefully. See
[`pico-watchdog.md`](pico-watchdog.md).

## Hardware / power topology

```
100W panel ─► PWM controller ─► 12.8V 20Ah LiFePO4
                  │  (RS485)          │
                  │                   ├──► 12V→5V buck (5A+), ENABLE gated by Pico ──► Pi 5 (8GB)
                  │                   │                                                     │
                  │                   ├──► Pico 2 W (own regulator, always-on, ~0.1–0.3W)  └──USB──► SIM7600G-H Dongle (~1–2W)
                  └── RS485 (Modbus RTU) ──► MAX485 ──► Pico UART
```

| Component | Part | Notes |
|---|---|---|
| Compute | Raspberry Pi 5 (8GB) | ~2.7W idle; event-capture camera, no continuous on-board AI |
| Panel | EcoWorthy 100W (ECOM100W) | baseline |
| Controller | EcoWorthy ECO-CON-PWM20A2.1 (PWM) | RS485 Modbus + BW0F BT/WiFi dongle |
| Battery | EcoWorthy 12.8V 20Ah LiFePO4 | 256 Wh nominal, ~205 Wh usable |
| Power controller | Raspberry Pi Pico 2 W | gates Pi 5V, watchdog, wake-on-recharge |
| Uplink + GNSS | Waveshare SIM7600G-H **4G Dongle** | USB plug-in; 4G/3G/2G global bands; GPS/BeiDou/Glonass/LBS; AT&T compatible |
| RTK base station | SparkFun ZED-F9P (roadmap) | see [RTK section](#rtk-base-station--roadmap) |

### Uplink + GNSS (SIM7600G-H 4G Dongle)

The dongle plugs into the Pi's USB port and is powered from it — so the modem
is off when the Pi is off. This is intentional: the Pico's safety loop is fully
autonomous and never needs the internet; the cellular uplink is only needed when
the Pi is running (remote access, OTA, HA relay, telemetry sync).

On Pi OS the dongle enumerates as a USB CDC-ECM/RNDIS network adapter +
several serial ports. The standard integration stack:

```
ModemManager   → manages modem state, SIM, bearer (AT&T LTE)
NetworkManager → manages wwan0, sets default route
gpsd           → reads NMEA from /dev/ttyUSB_gnss (stable udev name)
chrony         → uses gpsd as stratum-1 NTP source for the mesh
```

Typical port assignments (set stable udev names in `pi-setup.sh` to avoid
index shifts on reboot):

| Port | Function |
|---|---|
| `ttyUSB0` | AT commands (ModemManager) |
| `ttyUSB1` | NMEA / GNSS stream (`gpsd`) |
| `ttyUSB2` | Diagnostic |

The dongle's GNSS provides **coarse position + time** (GPS/BeiDou/Glonass).
This is sufficient for `gpsd`/`chrony` (stratum-1 NTP for the mesh) and a
geographic reference. It **cannot** act as an RTK base station — that requires
a precision receiver outputting raw RTCM3 observations (see roadmap below).

### RTK base station — roadmap

The gateway is the natural host for an RTK base station once RTK rover
capability is added to the platform. Hardware already validated in the LandPlan
survey stick: **SparkFun ZED-F9P** (or equivalent F9P breakout).

When implemented:
- ZED-F9P connects to the Pi over USB (or UART)
- `str2str` (RTKLIB) reads RTCM3 observations from the F9P
- Options for correction broadcast:
  - **NTRIP caster** — corrections pulled by rovers over cellular/mesh
  - **Mesh multicast** — RTCM3 broadcast over BATMAN-adv for on-property rovers
  - **LoRa fallback** — long-range broadcast for areas outside WiFi mesh range
- The SIM7600G-H dongle remains the internet uplink; the F9P is an additional
  USB device alongside it

The SIM7600G-H GNSS is **not** used for RTK base positioning — the F9P has its
own high-precision GNSS front-end and antenna.

> **Not in scope for v1.** Mark as a roadmap item; no code changes needed now.

### Starlink — OFF the solar budget
Starlink Mini draws ~15W idle / 17–40W active / 60W boot (360–960 Wh/day). It is
powered only when a human is present with auxiliary power. Surface its presence
in HA, but never run it on battery alone.

## Operating system

Run **Raspberry Pi OS Lite (64-bit) + Home Assistant in a container (or HA
Supervised)** — *not* the locked-down HAOS appliance. BATMAN-adv,
hostapd/dnsmasq, the RS485/GPIO power service, Tailscale, and OTA all need a
general-purpose OS, and this keeps tooling consistent with the field node
(systemd, Tailscale, the rsync-deploy pattern in [`setup-node.md`](setup-node.md)).

Services: Home Assistant · Mosquitto · BATMAN-adv + hostapd/dnsmasq · Tailscale ·
ModemManager + NetworkManager + `gpsd` + `chrony` (SIM7600G-H dongle) · gateway
camera · gateway power brain.

## Software power brain (`packages/gateway-node`)

Consumes the Pico's MQTT telemetry and runs the shared
[`smartfarmview-power-policy`](../packages/power-policy/) state machine (same
SoC-hysteresis + solar end-of-day projection as the field node). Each mode stops
a **cumulative** set of services; relaxing the mode restarts them. `dry-run` by
default during the baseline phase. See the
[package README](../packages/gateway-node/README.md) for config.

### MQTT contract
Pico → gateway, topic `securitymesh/gateway/pico/telemetry`:
```json
{"soc_pct": 78, "current_ma": -820.0, "voltage_v": 13.1, "gate_state": "pi_on"}
```
`current_ma`: + charging, − discharging. `soc_pct` is required; the Pico computes
it from the LiFePO4 voltage so the gateway stays chemistry-agnostic.

Gateway → MQTT: `securitymesh/<node>/power` (mode + projection, retained) and
`securitymesh/gateway/pi/heartbeat` (watched by the Pico's hardware watchdog).

## Power budget — predictions to validate

Estimated continuous load (Pi 5 + NVMe + SIM7600G-H dongle ~1–2W + mesh + camera
+ Pico + buck losses): **~7–9W avg**, peaks 12–15W → **~170–220 Wh/day**.

PWM harvest from the 100W panel (effective peak ≈ Imp×Vbatt ≈ ~73W):

| Season | ~PSH | Harvest/day | vs ~220 Wh load |
|---|---|---|---|
| Summer | ~5 | ~275 Wh | surplus |
| Shoulder | ~3.5 | ~190 Wh | ~breakeven (ECO/LOW) |
| Winter | ~2 | ~110 Wh | **deficit** |

Battery autonomy ≈ **20h** from full to cutoff at ~10W with no sun. **Prediction:**
outside peak summer the system *will* exercise the Pico cutoff/recovery cycle —
exactly the behaviour we want to observe. **Instrument everything** (SoC, current,
daily Wh in/out, mode transitions, cutoff/recovery events) → MQTT → HA history, so
predictions become measurements that size the production kit (likely ~2× panel +
battery and MPPT, TBC by data).

## Verification

1. **Bench, no solar:** vary a simulated battery voltage → confirm graceful
   `SHUTDOWN_REQ` → grace → cut, then recovery re-power above the higher
   threshold; confirm zero Pi draw when gated off.
2. **RS485:** confirm the Pico decodes voltage/current/SoC from the
   ECO-CON-PWM20A2.1; cross-check vs the BW0F app and a multimeter, then fix the
   register map in `pico-watchdog/firmware/main.py`.
3. **Watchdog:** hang the Pi → confirm heartbeat-loss reboot.
4. **Software brain:** `pytest` the (pure) policy; feed synthetic SoC/solar series.
5. **HA integration:** verify MQTT entities + a low-battery shutdown automation.
6. **Field soak:** deploy and log a multi-day dataset across weather; review daily
   Wh + cutoff/recovery frequency to size production.

## Open items
- Confirm ECO-WORTHY Modbus register map (voltage/current indices + scaling).
- Tune gate thresholds + LiFePO4 SoC curve from the field-soak dataset.
- Pick the 12V→5V buck (5A+, switchable EN) for Pi 5 peaks.
- Tailscale-on-Pico is not native — remote access is Pi-proxied (see
  [`pico-watchdog.md`](pico-watchdog.md)).
- Add `pi-setup.sh` phase for ModemManager, NetworkManager, `gpsd`, `chrony`,
  and udev stable names for the SIM7600G-H USB ports.
- **Roadmap:** SparkFun ZED-F9P RTK base station + `str2str` NTRIP/mesh broadcast
  (same receiver as the LandPlan survey stick; see RTK section above).
