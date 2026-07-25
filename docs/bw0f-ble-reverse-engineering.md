# BW0F BLE Reverse Engineering — Research Log

EcoWorthy BW0F Bluetooth module attached to EcoWorthy PWM20A solar charge controller.
Goal: read solar telemetry (SoC, voltage, current) on the Pico 2W via BLE for power management decisions.

---

## Hardware Setup

- **BW0F module** plugged into the PWM20A's RJ45 RS485 port
- The RJ45 port powers the BW0F (5V on pins 7/8) and provides RS485 data (A+ pin 1, B− pin 5, GND pin 2)
- BW0F reads RS485 from the controller and re-broadcasts via BLE
- The RS485 protocol is proprietary — not standard Modbus RTU
- BW0F BLE MAC: `7C:3E:82:AB:C9:50`
- Pico 2W: MicroPython v1.28.0 (2026-04-06), RP2350, CYW43439 chip, btstack BLE stack

---

## BLE GATT Structure (confirmed by discovery)

```
Service 0x1801 (Generic Attribute)  handles 1–4
  Char 0x2A05 (Service Changed)     vh=3
  CCCD                              handle=4

Service 0x1800 (Generic Access)     handles 5–11
  Char 0x2A00 (Device Name)         vh=7
  Char 0x2A01 (Appearance)          vh=9
  Char 0x2A04 (Conn Params)         vh=11

Service 0xFFF0 (Custom)             handles 12–17
  Char 0xFFF1                       vh=14  props=NOTIFY
    CCCD descriptor                 handle=15   ← subscribe here
  Char 0xFFF2                       vh=17  props=WRITE (with response)
                                            ← NO WRITE_WITHOUT_RESPONSE property
                                            ← no descriptors
```

Key handles:
- `15` — CCCD for 0xFFF1 (write `\x01\x00` to enable notifications)
- `17` — 0xFFF2 write target (write WITH response, type=1)
- `14` — 0xFFF1 notify value handle

---

## Notification Packet Types

The BW0F sends three types of notifications on 0xFFF1 (vh=14):

### A1 — Real-time telemetry (84 bytes)
First byte `0xA1`. Sent every ~800ms, alternating with A2.

#### Parse map (big-endian 16-bit words unless noted)
| Offset | Size | Description | Example |
|--------|------|-------------|---------|
| 0 | 1 | Packet type (`0xA1`) | `A1` |
| 4 | 1 | Battery SoC % (single byte) | `0x65` = 101 ≈ 100% |
| 16–17 | 2 | Charging current × 0.1A | `0x000C` = 1.2A |
| 18–19 | 2 | Battery voltage × 0.1V | `0x0083` = 13.1V |
| 20–21 | 2 | Load current × 0.1A | `0x0000` = 0.0A |
| 22–23 | 2 | Panel voltage × 0.1V (= battery in PWM mode) | `0x0083` = 13.1V |
| 30–31 | 2 | Controller temperature °C | `0x0019` = 25°C |
| 32–33 | 2 | Battery temperature °C | `0x0019` = 25°C |
| 40–41 | 2 | Charging power W | `0x0008` = 8W |
| 52–53 | 2 | Total energy (×0.1 Wh?) | `0x13FC` = 5116 |
| 56–57 | 2 | Charge stage (0=off 1=bulk 2=boost 3=float 4=equalize) | `0x0004` = equalize |
| 60–61 | 2 | LVD cutoff voltage × 0.1V | `0x0070` = 11.2V |
| 62–63 | 2 | Boost/absorption voltage × 0.1V | `0x0093` = 14.7V |
| 64–65 | 2 | Float voltage × 0.1V | `0x0082` = 13.0V |
| 66–67 | 2 | Equalize voltage × 0.1V | `0x0091` = 14.5V |
| 70–74 | — | `0xFFFF` (unused) | |
| 76–77 | 2 | Max panel voltage × 0.1V? | `0x012C` = 30.0V |
| 82–83 | 2 | Checksum (varies per packet) | `0x44BB`, `0x2917`, `0xE35D` |

Full sample packet:
```
A100 0000 6500 0000 0000 0101 0344 0001 000C 0083 0000 0083 0001 0000
0001 0019 0019 002E 0000 0000 0008 0000 0001 0002 0000 0000 13FC 0001
0004 0001 0070 0093 0082 0091 0091 FFFF FFFF FFFF 012C 0001 0001 44BB
```

#### Python parse snippet
```python
data = bytes(notify_data)          # 84 bytes
soc     = data[4]                  # %
chg_i   = ((data[16]<<8)|data[17]) * 0.1   # A
batt_v  = ((data[18]<<8)|data[19]) * 0.1   # V
load_i  = ((data[20]<<8)|data[21]) * 0.1   # A
ctrl_t  = (data[30]<<8)|data[31]            # °C
batt_t  = (data[32]<<8)|data[33]            # °C
chg_pwr = (data[40]<<8)|data[41]            # W
chg_stg = (data[56]<<8)|data[57]            # 0–4
lvd     = ((data[60]<<8)|data[61]) * 0.1   # V
v_boost = ((data[62]<<8)|data[63]) * 0.1   # V
v_float = ((data[64]<<8)|data[65]) * 0.1   # V
```

### A2 — Secondary data (100 bytes)
First byte `0xA2`. Alternates with A1 at ~800ms. Same SoC byte at offset 4. Mostly zeros — appears to carry load/historical data. Checksum `0x79DF` appears constant (suspicious).

### FF10 — Periodic heartbeat (5 bytes)
`FF 10 00 4C 30`. Sent every ~8 seconds. Purpose unknown (keepalive?).

### AA_ERR — Error response (5 bytes)
`AA AA AA AA AA`. Sent in response to unrecognized writes to 0xFFF2. Means "unknown command."

---

## What Works

| Test | Result |
|------|--------|
| BLE scan — find BW0F | ✓ reliably found |
| Connect to BW0F | ✓ (when phone is disconnected) |
| MTU negotiation to 247 bytes | ✓ (`bt.config(mtu=256)` before `bt.active(True)`) |
| Full GATT service/char/descriptor discovery | ✓ |
| CCCD write (handle 15, `\x01\x00`, type=1) | ✓ status=0 |
| Write to 0xFFF2 and receive AA_ERR notification | ✓ 5-byte AA_ERR arrives on every write |
| Parse A1 packet on Mac (Python 3) | ✓ all fields confirmed |

---

## What Does NOT Work

| Test | Result |
|------|--------|
| A1/A2 auto-push after CCCD subscription | ✗ — never arrives |
| FF10 heartbeat reception | ✗ — never arrives |
| Any FFF2 command triggering A1/A2 data | ✗ — all return AA_ERR |
| `bt.config(bond=True, mitm=True, io=3)` | ✗ — `ValueError: unknown config param` |
| `bt.gap_pair(conn_handle)` | ✗ — method does not exist in v1.28.0 |
| `bt.config(rxbuf=512)` | ✗ — `rxbuf` not supported |
| CCCD write with type=0 (write-without-response) | ✗ — no notifications |
| CCCD value `\x03\x00` (notify + indicate) | ✗ — no notifications |
| GATT Read of 0xFFF1 value (vh=14) | ✗ — no data triggered |
| Subscribing from inside IRQ event 7 handler | ✗ — returns ATT status=31 and BW0F disconnects |

---

## Commands Tried on 0xFFF2 (all returned AA_ERR)

Tested with both type=0 and type=1 writes:

```
\x00          \x01          \x01\x00      \x01\x01
\xA0          \xA0\x00      \xA0\x01      \xA0\x03
\xA0\x00\x00\x00            \xA0\x01\x00\x00
\xA0\x00\x00\x00\x00        \xA0\x00\x00\x00\x00\x00\x00\x00
\xA5\x00\x01\xA6            \xA5\x01\x00\xA6    \xA5\x01\x01\xA7
\xA5\x40\x01\xE6            \xA5\xFF\x00
\xA1          \xA2          \xB0          \xB0\x00      \xB0\x01
\xC0          \xF0          \xFF
\x10          \x11          \x12          \x55
\xa0\x00\x00\xa0  (XOR checksum variants)
\xff\x03\x01\x00\x00\x10\x00\x00   (Modbus-style)
\x01\x03\x00\x00\x00\x20           (short Modbus)
```

Not yet tried: full single-byte sweep (0x00–0xFF), multi-byte with correct checksum.

---

## Key Findings and Conclusions

### 1. BW0F is a push device (confirmed via nRF Connect log)
Data arrives ~750ms after CCCD subscription with **no write to 0xFFF2 needed** when connecting from iOS nRF Connect. Packets arrive at ~800ms intervals alternating A1/A2, with FF10 heartbeat every ~8s.

### 2. BW0F does NOT push to the Pico
Despite identical GATT subscription (same handle, same bytes, status=0), the BW0F never sends A1/A2 notifications to the Pico. It only responds to FFF2 writes with AA_ERR.

### 3. Small notifications work; large ones never arrive
5-byte AA_ERR responses arrive reliably on every FFF2 write. 84-byte A1 and 100-byte A2 packets never arrive. This could mean either:
- The BW0F is genuinely not sending them (most likely — BW0F is gating on bonding state)
- MicroPython is silently dropping large notifications (less likely — 84 bytes < ring buffer)

### 4. The BW0F likely requires bonding before streaming
nRF Connect on iOS pairs transparently via CoreBluetooth when a peripheral requests it. Once bonded, iOS recognizes the device and the BW0F auto-pushes to that bonded MAC. The Pico is an unknown (unbonded) client — the BW0F accepts its CCCD write but withholds the notification stream.

Evidence: every FFF2 command returns an error response (the BW0F IS talking to us), but NO spontaneous A1/A2 arrives — even in 30+ second listening sessions where 37+ packets should have arrived at 800ms cadence.

### 5. MicroPython v1.28.0 on RP2350 has no pairing API
`gap_pair()` is absent from `dir(bt)`. `bt.config(bond=True)` raises ValueError. There is no way to initiate BLE pairing from MicroPython on the Pico 2W with current firmware.

### 6. Connection stability
The BW0F accepts only one BLE connection at a time. When the phone (EcoWorthy app or nRF Connect) is connected, the Pico scan returns immediately with no results. When the phone disconnects, the BW0F typically takes 2–5 seconds (BLE supervision timeout) before advertising again. Using airplane mode on the phone is more reliable than just closing apps — iOS may maintain background BLE connections.

---

## MTU Notes

- `bt.config(mtu=256)` must be called **before** `bt.active(True)`
- Negotiated MTU = 247 bytes (BW0F's maximum)
- Max notification payload at MTU=247: 244 bytes — sufficient for A1 (84 bytes) and A2 (100 bytes)
- `bt.config(rxbuf=N)` is NOT supported on this firmware build

---

## Error Reference

| Error / Status | Meaning |
|----------------|---------|
| `AA AA AA AA AA` (5-byte notification) | Unknown/unrecognized command sent to 0xFFF2 |
| `ValueError: unknown config param` | `bt.config(bond=True/mitm=True/io=N)` not supported |
| `OSError: [Errno 114] EALREADY` | Attempted GATT operation after disconnect (race condition) |
| CCCD write status=31 | Called `gattc_write` from inside IRQ event 7 (too early — GATT client not ready) |
| Immediate disconnect (~230ms) | BW0F still connected to phone; Pico connected during supervision timeout window |

---

## Scripts Written (in /tmp/ on dev Mac)

| File | Purpose | Key Result |
|------|---------|-----------|
| `ble_connect.py` | Basic connect + GATT discovery | Confirmed service/char handles |
| `ble_pair_connect.py` | Attempt pairing | `gap_pair()` doesn't exist; pairing not needed for CCCD write |
| `ble_subscribe.py` | Subscribe + send FFF2 commands | Got AA_ERR for all commands |
| `ble_listen.py` | Passive + command phase | 0 spontaneous notifications |
| `ble_passive.py` | 60s passive listen | 0 notifications (MTU was 23 — too small) |
| `ble_mtu_probe.py` | MTU exchange + command test | Scan failed (phone connected) |
| `ble_bw0f_reader.py` | MTU=247, CCCD subscribe, parse A1 | 0 notifications despite stable connection |
| `ble_discover_sub.py` | Full GATT discovery then subscribe | status=0, CCCD confirmed h=15, 0 notifications |
| `ble_cccd_noack.py` | CCCD with type=0, type=1, 0x0300 | All 0 notifications |
| `ble_all_events.py` | Log every IRQ event | Wrote from IRQ event 7 → status=31 → disconnect |
| `ble_probe_v2.py` | rxbuf=512, immediate subscribe | rxbuf unsupported; status=31 from event 7 write |
| `ble_probe_v3.py` | Subscribe after MTU, log event 27 | 230ms disconnect (phone reconnected) |
| `ble_small_notify.py` | Write FFF2 every 2s, count responses | **15/15 AA_ERR received — small notify pathway works** |
| `ble_fuzz_fff2.py` | Try 25+ commands type=0 | All AA_ERR |
| `ble_a0_cmd.py` | A0-family commands type=1 | All AA_ERR (19 commands) |
| `ble_read_trigger.py` | Read FFF1 vh=14 to trigger push | Not yet run |
| `parse_bw0f.py` | Offline A1 packet parser (Mac) | All fields confirmed correct |

---

## Proposed Next Steps

### Option A — Pair via gateway Pi (recommended)
Use Python `bleak` on the Pi 5 (sfv-gateway) to connect, pair, and stream BW0F data. BlueZ on Linux handles bonding correctly. Once paired, the Pi can:
- Publish battery telemetry to MQTT (`securitymesh/bw0f/telemetry`)
- Or forward over UART to the Pico on the GP14/GP15 lines (not yet wired)

```bash
pip install bleak
# bleak handles pairing automatically on Linux via BlueZ
```

### Option B — Direct ADC voltage measurement on Pico
Add a resistor voltage divider (e.g. 100kΩ / 10kΩ) from the 12V battery rail to a Pico ADC pin (GP26–GP28). Gives accurate real-time battery voltage without BLE dependency. Simpler and more reliable. Does not give SoC, charge stage, or current — only voltage.

### Option C — Brute-force FFF2 command sweep
Try all 256 single-byte values (0x00–0xFF) on FFF2 with type=1. If any triggers a non-AA response, that's the init command. Would take ~6 minutes. Low probability of success given the bonding hypothesis.

### Option D — Custom MicroPython build
Build MicroPython from source with `MICROPY_PY_BLUETOOTH_BTSTACK=1` and security features enabled, adding `gap_pair()` support for RP2350. Feasible but time-consuming.

---

## nRF Connect Log Reference (working session)

```
[22:15:02.026] Normal: Scanner On.
[22:15:02.493] Normal: Device Scanned.
[22:15:15.264] Normal: Connected.
[22:15:15.714] Normal: Discovered 0000FFF0-0000-1000-8000-00805F9B34FB Services.
[22:15:15.773] Normal: Service Discovery returned nil Services.
[22:15:15.836] Normal: Discovered FFF1 and FFF2 Characteristics for Service FFF0.
[22:15:15.893] Normal: Discovered Client Characteristic Configuration Descriptors for FFF1.
[22:15:15.894] Normal: FFF2 has no Descriptors.
[19:09:52.313] Setting Boolean true for Notifying Characteristic FFF1
[19:09:53.056] Updated Value to A100 0000 6500...   ← first A1 packet, 743ms after CCCD enable
```

nRF Connect sequence: connect → discover FFF0 service → discover FFF1/FFF2 chars → discover CCCD → user enables notifications → data flows. No write to FFF2 at any point.
