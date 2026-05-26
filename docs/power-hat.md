# Power HAT — Pi Zero UPS HAT (B) by WatangTech / SeenGreat

Reference: https://seengreat.com/wiki/175/

This is the first power unit under test on field nodes. All power monitoring code is modular — see `packages/field-node/src/field_node/power/` — and should be easy to swap for a different HAT.

---

## Hardware Overview

| Spec | Value |
|---|---|
| Model | Pi Zero UPS HAT (B) |
| Output | 5V regulated |
| Max output current | ~1.8A (fully charged) |
| Solar input range | 5V – 24V |
| USB input | 5V, max 5W |
| Solar charging | Up to ~5W |
| Dimensions | 65mm × 30mm |

### Onboard ICs

| Chip | Function |
|---|---|
| INA219 | Battery voltage, current, and power monitoring (I2C) |
| ETA6003 | Lithium battery charging controller |
| CN3791 | Solar MPPT charging management |
| TPS61088 | Boost converter |
| HM1160 | LED fuel gauge driver |

---

## I2C Battery Monitoring (INA219)

- **I2C address:** `0x43`
- **Bus:** I2C bus 1 (`/dev/i2c-1`)

### Enabling I2C on the Pi

I2C is not enabled by default on Raspberry Pi OS Lite. Enable it and add the user to the `i2c` group, then reboot:

```bash
sudo raspi-config nonint do_i2c 0
sudo usermod -aG i2c techno
sudo reboot
```

> **Note:** `pi-setup.sh` does not currently enable I2C automatically — this is a manual step after first boot. It should be added to the provisioning script for future nodes.

After reboot, verify the chip is detected:

```bash
i2cdetect -y -a 1
# Should show 0x43 in the output grid
```

If `/dev/i2c-1` does not exist after running raspi-config, a reboot is required before the device node appears.

### Readings

| Metric | Notes |
|---|---|
| Battery voltage (V) | Direct cell voltage |
| Current (mA) | Positive = charging, **negative = discharging** |
| Power (mW) | Calculated from voltage × current |

A negative current reading means the battery is supplying power to the Pi (no solar/USB input, or input is insufficient to cover the load).

### Failure Mode

If `/dev/i2c-1` does not exist when the field-node service starts, the power monitor initialises in degraded mode — the service continues running and all other features (camera, MQTT, telemetry) remain functional. Battery fields are omitted from telemetry until I2C is enabled and the service restarted.

Log output when I2C is missing:
```
[warning] power_monitor_unavailable  driver=ina219_hat  error="[Errno 2] No such file or directory: '/dev/i2c-1'"
```

---

## Battery Level LEDs

Four LEDs on the HAT provide a visual fuel gauge:

| LEDs on | Voltage range | Approximate charge |
|---|---|---|
| 4 | 3.87 – 4.2V | 100% |
| 3 | 3.70 – 3.87V | 75% |
| 2 | 3.55 – 3.70V | 50% |
| 1 | 3.40 – 3.55V | 25% |

A separate **CHRG** LED lights during active charging.

---

## Solar MPPT DIP Switch Configuration

A 6-position DIP switch sets the MPPT voltage to match the solar panel's maximum power point. **Only one switch should be ON at a time.**

| Switch position | Voltage |
|---|---|
| 1 | 5V |
| 2 | 6V |
| 3 | 9V |
| 4 | 12V |
| 5 | 18V |
| 6 | 24V |

Set the switch to the voltage closest to your panel's maximum power point (Vmp). For most small 12V panels, position 4 (12V) is appropriate. Correct selection maximises charging efficiency.

> **Field note:** Set the DIP switch before deploying — it is awkward to adjust inside an enclosure. Document the panel Vmp in the node's deployment log.

---

## Important Operating Notes

- Solar and USB inputs **cannot charge simultaneously**
- New or long-stored batteries may need initial USB charging to activate the protection circuit before solar charging will begin
- Power the Pi off before assembling or disconnecting the HAT
- Connect the battery **after** mechanical assembly is complete
- Avoid sustained loads that cause repeated Pi reboots — the protection circuit will trip under repeated short-circuit/overload conditions
- The HAT does not expose a low-battery shutdown signal to the Pi GPIO — use the battery voltage reading from the INA219 to implement software-controlled graceful shutdown in a future release

---

## Power Monitoring Integration

See [`packages/field-node/src/field_node/power/`](../packages/field-node/src/field_node/power/) for the modular Python driver.

The power monitor publishes to MQTT on the standard telemetry interval alongside CPU and storage metrics:

```json
{
  "ts": 1234567890.0,
  "cpu_temp": 47.2,
  "storage_pct": 5.1,
  "battery_voltage": 3.94,
  "battery_current_ma": -210.5,
  "battery_power_mw": -830.2,
  "battery_discharging": true
}
```

`battery_discharging: true` means the battery is supplying power. Home Assistant sensor entities for voltage, current, and power are registered via MQTT discovery on every node startup — no manual HA configuration required.

---

## Swap Guide

To replace this HAT with a different power management unit:

1. Implement the `PowerMonitor` ABC defined in [`packages/field-node/src/field_node/power/base.py`](../packages/field-node/src/field_node/power/base.py):

```python
class PowerMonitor(ABC):
    def read(self) -> PowerReading: ...
    def close(self) -> None: ...
```

2. Add the new implementation as `packages/field-node/src/field_node/power/<product_name>.py`

3. Register the driver name in `main.py`'s `_load_power_monitor()` function alongside `ina219_hat`

4. Set `FIELD_NODE_POWER_MONITOR=<product_name>` in `/opt/field-node/.env` on the Pi

5. The telemetry payload, MQTT discovery, and HA entities require no changes — `PowerReading.voltage_v`, `.current_ma`, `.power_mw`, and `.is_discharging` are the only fields consumed upstream

---

## Outstanding Items

- [ ] Add I2C enable + `i2c` group to `pi-setup.sh` so future nodes don't need a manual step
- [ ] Implement low-battery graceful shutdown using voltage threshold from INA219
- [ ] Validate INA219 calibration values against a multimeter reading on LandPlanMesh1
- [ ] Document the specific battery and solar panel specs for LandPlanMesh1 once confirmed
