# Field Node Setup Guide — LandPlanMesh1

Step-by-step record of how the first prototype field node was assembled and configured. Intended to become the repeatable standard procedure for all SecurityMesh field nodes.

---

## Hardware

| Component | Details |
|---|---|
| Compute | Raspberry Pi Zero 2 W (header pins soldered) |
| Camera | Raspberry Pi Camera Module Rev 1.3 (OV5647, 5MP) |
| Power / UPS | WatangTech Pi Zero UPS HAT (B) — 5V–24V Solar MPPT charging, battery level indicator, UPS |
| Storage | 32 GB microSD (industrial-grade recommended for future nodes) |

---

## Step 1 — Flash the OS

1. Download **Raspberry Pi OS Lite 64-bit** via Raspberry Pi Imager.
2. In Imager advanced settings:
   - Set hostname: `LandPlanMesh1`
   - Set username: `techno`
   - Enable SSH (password auth)
   - Configure WiFi SSID and password
3. Flash to the 32 GB microSD card.
4. Fit the UPS HAT onto the Pi Zero 2 W GPIO header.
5. Connect the Pi Camera flat cable to the CSI port.
6. Insert the microSD and power on.

---

## Step 2 — SSH in

From the development machine (Mac on the same network):

```bash
ssh techno@LandPlanMesh1.local
```

Expected prompt:
```
techno@LandPlanMesh1:~ $
```

> mDNS (`.local`) resolves automatically on the local network via avahi. If it fails, scan for the IP using `arp -a` or your router's device list.

---

## Step 3 — Update the OS

```bash
sudo apt update && sudo apt full-upgrade -y
```

---

## Step 4 — Install camera tools

Raspberry Pi OS Lite does not include the camera utilities. Install `rpicam-apps`:

```bash
sudo apt install -y rpicam-apps
```

Verify the camera is detected:

```bash
rpicam-hello --list-cameras
```

> **Note:** The Camera Module v1.3 uses the OV5647 sensor. It is supported by `libcamera` on 64-bit Pi OS without legacy camera mode.

Expected output:
```
Available cameras
-----------------
0 : ov5647 [2592x1944 10-bit GBRG] (/base/soc/i2c0mux/i2c@1/ov5647@36)
    Modes: 'SGBRG10_CSI2P' : 640x480 [58.92 fps - (16, 0)/2560x1920 crop]
                             1296x972 [46.34 fps - (0, 0)/2592x1944 crop]
                             1920x1080 [32.81 fps - (348, 434)/1928x1080 crop]
                             2592x1944 [15.63 fps - (0, 0)/2592x1944 crop]
```

---

## Step 5 — Install TailScale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

The command will print an authentication URL. Open it in a browser and approve the node joining your tailnet.

Once approved, confirm the node has a TailScale IP:

```bash
tailscale ip -4
```

Expected output: a `100.x.x.x` address. The node will also be reachable via MagicDNS as `landplanmesh1` from any device on the tailnet.

---

## Step 6 — Provision the node (pi-setup.sh)

Copy the setup scripts from the dev machine, then run:

```bash
scp packages/field-node/scripts/* techno@landplanmesh1:~/
ssh techno@landplanmesh1 'bash ~/pi-setup.sh'
```

This script is idempotent — safe to re-run. It:
- Installs `python3`, `python3-venv`, `python3-picamera2`, `rpicam-apps`, `git`, `rsync`
- Adds `techno` to the `video` group for camera access
- Creates `/opt/field-node/` and `/opt/field-node/captures/`
- Creates a Python venv at `/opt/field-node/.venv` with `--system-site-packages` (required for picamera2)
- Installs the `field-node.service` systemd unit
- Runs `rpicam-hello --list-cameras` to verify camera detection

> **Note:** Raspberry Pi OS (2026) ships Python 3.13. The package `python3.11` does not exist — use `python3` and `python3-venv` instead.

Expected camera output at end of script:
```
Available cameras
-----------------
0 : ov5647 [2592x1944 10-bit GBRG] (/base/soc/i2c0mux/i2c@1/ov5647@36)
```

---

## Step 7 — Enable the service for auto-start on boot

```bash
sudo systemctl enable field-node
```

Expected output:
```
Created symlink '/etc/systemd/system/multi-user.target.wants/field-node.service' → '/etc/systemd/system/field-node.service'.
```

The service will now start automatically whenever the Pi powers on.

To check logs at any time:
```bash
journalctl -u field-node -f
```

---

## Step 8 — First deploy via GitHub Actions

Push any change to `packages/field-node/**` on `main` to trigger the deploy workflow, or trigger it manually:

```bash
gh workflow run deploy-field-node.yml --repo futuretechnotim-star/smartfarmview-web --ref main
```

The workflow connects to the Pi over TailScale (IP: `100.70.1.11`), rsyncs the code to `/opt/field-node/`, runs `pip install -e '.[hardware]'`, and restarts the service.

Verify the service came up:
```bash
systemctl status field-node --no-pager
```

Expected: `Active: active (running)` with camera initialization logs and a `camera_ready` info line.

> **Note:** An `mqtt_not_connected_skipping` warning is expected until a gateway MQTT broker is configured.

---

## Step 9 — Configure MQTT broker

Create `/opt/field-node/.env` on the Pi with broker credentials. This file is excluded from git and from rsync — it persists across deploys.

```bash
sudo tee /opt/field-node/.env << EOF
FIELD_NODE_MQTT_HOST=192.168.1.197
FIELD_NODE_MQTT_USERNAME=field-node
FIELD_NODE_MQTT_PASSWORD=<password>
EOF
sudo systemctl restart field-node
```

The MQTT broker is the Mosquitto add-on running on the Home Assistant Pi (`192.168.1.197:1883`). Authentication uses a dedicated HA user account (`field-node`) created in **Settings → People → Users**.

Verify connection in logs:
```bash
journalctl -u field-node -n 10 --no-pager | grep mqtt
```

Expected:
```
mqtt_connected   host=192.168.1.197 port=1883
discovery_published   component=sensor object_id=cpu_temp
discovery_published   component=sensor object_id=storage_pct
discovery_published   component=binary_sensor object_id=motion
```

---

## Step 10 — Verify Home Assistant auto-discovery

In Home Assistant: **Settings → Devices & Services → MQTT**

A device named `LandPlanMesh1` should appear automatically with 3 entities:
- **CPU Temperature** — updates every 60 seconds
- **Storage Used** — updates every 60 seconds  
- **Motion** — ON/OFF binary sensor (PIR not yet wired; will trigger on motion events)

No manual HA configuration required — entities are registered via MQTT discovery on every node startup.

---

<!-- Steps below are pending confirmation and will be added as setup progresses -->
