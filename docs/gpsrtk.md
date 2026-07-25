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

## ⚠️ Base-station / rover TODO — RTCM3 was disabled on I2C

This gateway is intended as the **RTK base station**; the **rover is a DIY
handheld survey stick** using the same ZED-F9P. The whole point is to make the
rover more accurate: two receivers plus a way to share the base's correction
data (RTCM3). **That correction-sharing path is not built yet** — it may run
over the field mesh or over the internet; this base station was only the first
step.

The fix above **disables RTCM3 output on the I2C port**. That is safe *right
now* because nothing consumes RTCM3 over I2C (the gateway only reads NAV-PVT,
and no rover link exists yet). `MON-COMMS` shows heavy TX on UART2, so if
corrections are ever taken off the module directly they should come from a UART,
**not** the I2C port — I2C must stay UBX-only or the DDC-overload symptom
returns. **When the correction-sharing link is designed, revisit this config:**
route RTCM3 out a UART (or read it from a source that isn't the NAV-PVT I2C
stream), and keep I2C dedicated to NAV-PVT.

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
