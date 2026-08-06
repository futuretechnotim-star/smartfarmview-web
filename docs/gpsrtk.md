# GPS-RTK Base Station — Intermittency Investigation

The gateway's RTK base station (SparkFun GPS-RTK2, ZED-F9P @ I2C `0x42`, bus 1)
intermittently stopped producing fixes. This doc records the investigation,
the **root cause (found 2026-07-25)**, the fix, and what still needs revisiting
when the rover/correction-sharing side is built.

**Status as of 2026-07-25: ROOT-CAUSED AND FIXED.** The module was fine; the
I2C port had NMEA + RTCM3 output enabled alongside UBX, which overran the
ZED-F9P's small DDC (I2C) transmit buffer and wedged its periodic-message
output. Disabling NMEA + RTCM3 on the I2C port makes NAV-PVT stream
continuously. See "Root cause" below.

**Update 2026-08-02:** base-station mode (survey-in → RTCM3 output → local
NTRIP caster) is now built — see "Base-station mode" below. It re-enables
RTCM3 on I2C, but mode-switched, never concurrent with NAV-PVT.

## Root cause

The ZED-F9P was configured to emit **three protocols on the I2C output port** —
UBX, NMEA, *and* RTCM3 (`CFG-I2COUTPROT-NMEA=1`, `CFG-I2COUTPROT-RTCM3X=1`,
confirmed live via `CFG-VALGET`). As a base station it produces large, bursty
RTCM3 correction messages. With all three sharing the small DDC TX buffer, the
buffer overran (`MON-COMMS` showed the I2C port at **`txPeak=100%`** with a TX
error flag set) and the module's **periodic** message scheduler stopped queuing
output. On-demand poll responses (`MON-VER`, `CFG-VALGET`) still worked the
whole time — which is exactly why it looked like a random "goes silent" fault
rather than an overload: the module wasn't dead, its streaming was wedged.

This explains every earlier observation:
- The brief bursts of fixes right after each reconfigure — the CFG-MSG momentarily
  reset the scheduler.
- The ~120s good/dead rhythm — one post-reconfigure burst held until it went stale.
- Why faster draining only "partially helped" — it eased buffer pressure but
  couldn't outrun bursty RTCM3.
- Why a hard power-cycle changed nothing — the config (in RAM) came back identical.

### Decisive evidence

Live on `sfv-gateway`, disabling **only** NMEA + RTCM3 output on the I2C port
(via `CFG-VALSET`, one variable changed):

| Config | Result |
| --- | --- |
| UBX + NMEA + RTCM3 on I2C (old) | `bytes_available` = 0 for ~230s straight; 0 NAV-PVT |
| UBX only on I2C (new) | NAV-PVT every second for 240s straight — 235/236 s, max gap 1.0s, **zero reconfigures** |

The single most important correction to the earlier investigation: the
"driver-class-fails-but-`gps_demo.py`-works" mystery was a **red herring**. A
probe using logic *identical* to `gps_demo.py` reproduced the failure exactly;
the demo's one 175s success simply caught a good window. It was never a code
difference, and never `UbxStreamParser` state.

## The fix (deployed in code — see `gps_driver.py` / `gps_protocol.py`)

`ZedF9pDriver._configure()` now sends a single **UBX-CFG-VALSET** that:
1. sets `CFG-I2COUTPROT-NMEA = 0` and `CFG-I2COUTPROT-RTCM3X = 0` (UBX-only on I2C),
2. sets `CFG-MSGOUT-UBX_NAV_PVT_I2C = 1` (NAV-PVT @ 1 Hz on I2C),
3. writes to **RAM + BBR + Flash** at startup so it survives reboots (previously
   RAM-only, so every power cycle reverted to the module default — NAV-PVT-off —
   until the service reconfigured; that was the boot-time restart storm), and
4. **verifies the UBX-ACK** (the old CFG-MSG was fire-and-forget).

The self-heal reconfigure (on stale / I2C error) is retained as a cheap safety
net but writes RAM only (no flash wear) and should now rarely fire.

Also fixed a latent parser bug in `UbxStreamParser.feed()`: a spurious/torn
`B5 62` sync followed by a large length field used to block the buffer forever
(head-of-line stall for the life of the process). It now treats an implausible
length (> `_MAX_UBX_PAYLOAD`) or a checksum failure as a bad sync and resyncs
one byte at a time. Not the cause of this outage, but a real robustness hole.

Test coverage: `build_cfg_valset`, `find_ack`, and the parser resync paths are
pure and unit-tested in [`test_gps_protocol.py`](../packages/gateway-node/tests/unit/test_gps_protocol.py);
`_gps_payload_fields()` / `_is_fix_stale()` remain tested in
[`test_sensors.py`](../packages/gateway-node/tests/unit/test_sensors.py).
`gps_driver.py` is hardware-dependent I2C glue and exempt per this repo's TDD
conventions.

## Base-station mode — built 2026-08-02

The correction-sharing path above is now built, and it **does** put RTCM3 back
on I2C — just never *concurrently* with NAV-PVT, which is the actual failure
mode from the root cause above (not "RTCM3 on I2C" per se). The gateway is a
single-owner I2C bus with three mutually-exclusive output modes, sequenced by
[`gps_base_mode.py`](../packages/gateway-node/src/gateway_node/gps_base_mode.py):

```
ROVER_NAV ──(gps_start_survey cmd)──► SURVEYING ──(svinValid)──► BASE_ACTIVE
   ▲                                                                   │
   └───────────────────────(gps_stop_base cmd)────────────────────────┘
```

- **ROVER_NAV** (default/today's behavior): `CFG-MSGOUT-UBX_NAV_PVT_I2C=1`,
  everything else off. Same config as the fix above.
- **SURVEYING**: NAV-PVT off, `CFG-TMODE-MODE=1` (survey-in armed),
  `CFG-MSGOUT-UBX_NAV_SVIN_I2C=1` to track `dur`/`meanAcc` progress. Entered
  manually via an MQTT command (`{"cmd": "gps_start_survey"}` on
  `securitymesh/{node}/cmd`, or the `button.gps_start_survey` HA entity) —
  not automatic.
- **BASE_ACTIVE**: once the module reports `svinValid`, NAV-SVIN off, the
  three RTCM3 messages on ([`gps_driver.py`](../packages/gateway-node/src/gateway_node/gps_driver.py)'s
  `enter_base_active()`) — station ARP (1005), GPS MSM7 (1077), GLONASS
  biases (1230). Deliberately minimal (not full 4-constellation MSM7) to keep
  DDC byte volume low while this is freshly live — expand later once
  confirmed stable over a real multi-hour run.

`gps_driver.py`'s `drain_rtcm3()` relays the raw BASE_ACTIVE I2C stream
(no UBX framing once RTCM3-only) to [`ntrip_caster.py`](../packages/gateway-node/src/gateway_node/ntrip_caster.py),
a local NTRIP caster (`http://<gateway>:2101/SFV_BASE` by default) serving
corrections on the field-mesh WiFi. This reuses NTRIP for the local hop too,
not just the survey stick's originally-designed
`caster → internet → phone → BLE` path — see
[`landplan/docs/mobile/requirements-gps.md`](../../landplan/landplan/docs/mobile/requirements-gps.md)
§6.5 — so the survey stick's Pi Zero only ever needs one NTRIP-client
implementation, whichever transport is live. Its own NTRIP client isn't built
yet; that's cross-repo work, out of scope here (see the Codex handoff for
what it needs).

### Survey-in duration/accuracy tradeoff

`gps_svin_min_dur_s` (default 300s) and `gps_svin_acc_limit_m` (default 2.5m)
set how long/precise the survey-in must be before the module reports
`svinValid` and RTCM3 starts. A longer survey narrows the base's *absolute*
position error, but for RTK the number that actually matters to the rover is
the base-to-rover *relative* geometry — corrections are differential, so a
base whose absolute position is off by a couple of meters still yields
centimeter-level rover accuracy relative to that (slightly-off) reference
point. A short, loose survey-in is therefore fine for day-to-day relative
survey work; only re-survey longer/tighter if the base's absolute coordinates
themselves need to be trustworthy (e.g. tying survey data to a known real-
world reference).

### Open risk — needs live confirmation

The DDC-overrun root cause above was diagnosed with UBX+NMEA+RTCM3 all
enabled at once; RTCM3-alone in BASE_ACTIVE hasn't been run live yet. Confirm
via `MON-COMMS` `txPeak`/error flags the same way this investigation did,
before trusting BASE_ACTIVE unattended for long periods.

## Retained mitigations (still active, now belt-and-suspenders)

1. **Staleness guard** — [`sensors.py`](../packages/gateway-node/src/gateway_node/sensors.py),
   `_is_fix_stale()` / `Settings.gps_fix_stale_timeout_s` (120s). A fix older
   than the timeout is published as `None`, so HA shows "Unknown" rather than a
   frozen position. Correct behaviour regardless of root cause: **never show
   stale GPS data as current.**
2. **I2C drain cadence decoupled from MQTT publish cadence** — `sensors.py`
   drains every ~1s independent of the 60s publish tick.
3. **Self-heal reconfigure** — `gps_driver.py`, RAM-only, on stale/I2C-error.

## Investigation timeline — theories tried (historical)

Kept for the record; note items 6-8 and the "open question" were superseded by
the root cause above.

1. **MQTT broker not up yet at boot** — a real, separate bug, fixed independently.
   Not the GPS cause.
2. **Config write silently not taking effect, one-shot at startup.** Led to the
   self-heal logic. Helped, didn't fully fix — because the real issue was buffer
   overload, not the config write.
3. **Power event on the GPS board itself.** Hard power-cycle tried. **Ruled out** —
   identical failure after.
4. **Shared I2C bus contention with `gateway-camera`'s PCA9685.** Stopping camera
   made it *worse*, not better. **Ruled out.**
5. **Marginal SDA/SCL solder joint.** Resoldered; identical pattern after.
   Module bench-tested healthy standalone. **Ruled out.**
6. **60s poll interval overflowing the DDC buffer.** Decoupled drain to ~1s.
   Partially helped — *right neighbourhood* (it is a DDC-buffer problem) but the
   overflow source was RTCM3/NMEA output, not our read cadence.
7. **The self-heal reconfigure is itself the disruptive trigger.** Disabling it
   gave zero frames — because without the periodic scheduler-reset the wedged
   output never recovered. Consistent with the buffer-overload cause.
8. **`smbus2` version/install mismatch.** **Ruled out.**

**Former "core mystery" (now resolved):** a standalone `gps_demo.py` ran clean
for 175s while the driver class got zero fixes in 5 minutes. This looked like a
code difference but was not — see "Decisive evidence" above. The demo caught a
good window; the class was tested during a wedged one. Same DDC-overload cause
for both.
