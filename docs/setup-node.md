# Field Node Setup Guide — LandPlanMesh1

Step-by-step record of how the first prototype field node was assembled and configured. Intended to become the repeatable standard procedure for all SecurityMesh field nodes.

---

## Hardware

| Component | Details |
|---|---|
| Compute | Raspberry Pi Zero 2 W (header pins soldered) |
| Camera | Arducam 16MP IMX519, 120° (D) M12 wide-angle lens (CSI) — needs `dtoverlay=imx519` |
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

> **Important — the Arducam IMX519 is not auto-detected.** Unlike the old OV5647,
> the IMX519 needs an explicit device-tree overlay. `pi-setup.sh` (Phase 8) sets
> this idempotently — it adds to `/boot/firmware/config.txt`:
> ```ini
> camera_auto_detect=0
> dtoverlay=imx519
> ```
> and reboots once when the overlay is first added. If the overlay is missing
> from the OS image, install Arducam's kernel driver:
> <https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver> (`-p imx519_kernel_driver`).
> Without the overlay, `rpicam-hello` reports **"No cameras available!"** and the
> field-node service logs `camera_unavailable` (it stays up and keeps publishing
> telemetry — a camera fault no longer takes the node dark).

After the overlay is active (post-reboot), verify the camera is detected:

```bash
rpicam-hello --list-cameras
```

Expected output (IMX519, 16MP):
```
Available cameras
-----------------
0 : imx519 [4656x3496 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx519@1a)
    Modes: 'SRGGB10_CSI2P' : 1280x720  [...]
                             1920x1080 [...]
                             2328x1748 [...]
                             3840x2160 [...]
                             4656x3496 [...]
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

---

## Gateway Node Setup (Pi 5 — sfv-gateway)

Hardware: Raspberry Pi 5 (8GB), microSD, SIM7600G-H dongle (USB).
OS: Raspberry Pi OS Lite 64-bit + Home Assistant Supervised.
Tailscale IP: `100.127.225.15`  SSH key: `~/.ssh/gateway_deploy`

### Step 1 — Flash the OS

1. Open **Raspberry Pi Imager** → Device: Pi 5, OS: Raspberry Pi OS Lite (64-bit).
2. **Edit Settings:**

   | Setting | Value |
   |---|---|
   | Hostname | `sfv-gateway` |
   | Username | `techno` |
   | SSH | Enable — password auth |
   | WiFi | lab/home network (initial setup only) |

3. Flash to microSD, boot the Pi.

### Step 2 — SSH in and bootstrap

From the dev Mac (once the Pi is on the network):

```bash
ssh techno@sfv-gateway.local
```

Authorize the Claude Code deploy key and enable passwordless sudo (one-time, password required):

```bash
# Paste the gateway_deploy public key
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKrxIT12QXbuZ3izLWPH3SgQBQyXv794T6TYQbLVKD1I claude-code-gateway" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
echo "techno ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/techno-nopasswd
sudo chmod 440 /etc/sudoers.d/techno-nopasswd
```

### Step 3 — OS update

```bash
sudo apt update && sudo apt full-upgrade -y
```

### Step 4 — Install HA Supervised dependencies

```bash
sudo apt install -y apparmor cifs-utils curl dbus jq libglib2.0-bin lsb-release \
  network-manager nfs-common systemd-journal-remote udisks2 wget systemd-resolved
sudo systemctl enable --now systemd-resolved
```

### Step 5 — Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker techno
```

### Step 6 — Install HA OS Agent

```bash
OS_AGENT_VERSION=$(curl -fsSL https://api.github.com/repos/home-assistant/os-agent/releases/latest | jq -r '.tag_name' | tr -d 'v')
curl -fsSL -o /tmp/os-agent.deb "https://github.com/home-assistant/os-agent/releases/latest/download/os-agent_${OS_AGENT_VERSION}_linux_aarch64.deb"
sudo dpkg -i /tmp/os-agent.deb
```

### Step 7 — Install HA Supervised

Pre-seed the machine type (required — non-interactive install fails without this):

```bash
curl -fsSL -o /tmp/homeassistant-supervised.deb \
  https://github.com/home-assistant/supervised-installer/releases/latest/download/homeassistant-supervised.deb
sudo dpkg -i /tmp/homeassistant-supervised.deb || true
echo 'homeassistant-supervised ha/machine-type select raspberrypi5-64' | sudo debconf-set-selections
sudo systemctl restart systemd-resolved
sudo DEBIAN_FRONTEND=noninteractive dpkg --configure homeassistant-supervised
```

> **Note:** The installer fails if DNS is broken. `systemd-resolved` must be running
> before `dpkg --configure` is called. If `version.home-assistant.io` doesn't resolve,
> restart `systemd-resolved` and retry.

HA pulls its containers in the background. Reachable at `http://<ip>:8123` within ~5 min.

### Step 8 — Install Tailscale (system service)

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up   # opens auth URL — approve in browser
tailscale ip -4     # confirm 100.x.x.x address
```

> Run Tailscale as a **system service** (not the HA add-on) so SSH, the deploy
> pipeline, and the power brain are all on the tailnet — not just HA traffic.

### Step 9 — HA onboarding + Mosquitto

1. Open `http://sfv-gateway.local:8123` and complete onboarding.
2. **Settings → Add-ons → Add-on Store → Mosquitto broker** — Install, enable
   Start on boot + Watchdog.
3. In Mosquitto **Configuration** tab, add logins:
   ```yaml
   - username: gateway-node
     password: "your-password"
   - username: pico-watchdog
     password: "your-password"
   ```
4. Start the add-on.

### Step 10 — Deploy gateway-node power brain

From the dev Mac:

```bash
cd smartfarmview-web

# Create opt directories (root-owned, pre-create as techno)
ssh -i ~/.ssh/gateway_deploy techno@sfv-gateway.local \
  "sudo install -d -o techno -g techno /opt/gateway-node /opt/power-policy"

# Sync packages
rsync -a -e "ssh -i ~/.ssh/gateway_deploy" packages/gateway-node/ techno@sfv-gateway.local:/opt/gateway-node/
rsync -a -e "ssh -i ~/.ssh/gateway_deploy" packages/power-policy/  techno@sfv-gateway.local:/opt/power-policy/

# Provision (installs venv, deps, systemd service)
ssh -i ~/.ssh/gateway_deploy techno@sfv-gateway.local "sudo bash /opt/gateway-node/scripts/pi-setup.sh"
```

Create `/opt/gateway-node/.env` on the Pi:

```bash
cat > /opt/gateway-node/.env << 'EOF'
GATEWAY_NODE_MQTT_USERNAME=gateway-node
GATEWAY_NODE_MQTT_PASSWORD=<mosquitto-password>
GATEWAY_NODE_HA_BASE_URL=http://localhost:8123
GATEWAY_NODE_HA_TOKEN=<ha-long-lived-token>
GATEWAY_NODE_SERVICE_CONTROL=dry-run
EOF
sudo systemctl start gateway-power
```

Generate the HA token at **Profile → Long-Lived Access Tokens**. Keep it secret.

### Step 11 — Verify

```bash
sudo systemctl status gateway-power --no-pager
journalctl -u gateway-power -n 20 --no-pager
```

Expected:
```
gateway_power_starting   node_id=sfv-gateway
mqtt_connected           host=127.0.0.1 port=1883
```

MQTT heartbeat (published every 30 s once Pico is wired):
```
topic: securitymesh/gateway/pi/heartbeat
```

---

## Gateway Node — Tailscale Funnel for Home Assistant

Funnel exposes the HA frontend at a stable public HTTPS URL
(`https://homeassistant.tail7b513f.ts.net`) without requiring callers to be on
the tailnet. Required for multi-user LandPlan field-node access.

### Prerequisites — Tailscale admin console

In the [Tailscale admin DNS settings](https://login.tailscale.com/admin/dns):

- **MagicDNS** — must be enabled
- **HTTPS Certificates** — must be enabled (separate toggle; this is the step
  most likely to be missing — the add-on logs `FATAL: Tailscale's HTTPS support
  is disabled` if it is off)

In **Access Controls**, the tailnet ACL must grant the `funnel` node attribute:

```json
"nodeAttrs": [
  {
    "target": ["autogroup:member"],
    "attr":   ["funnel"]
  }
]
```

### Step 1 — Trust the reverse proxy in HA

The Tailscale add-on acts as a reverse proxy to HA on port 8123. Without this,
HA rejects the proxied connections and the add-on logs
`FATAL: Unable to connect to Home Assistant as reverse proxy` in a tight loop.

Using the **File Editor add-on** (Settings → Add-ons → Add-on Store → File
Editor), open `/config/configuration.yaml` and add:

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1
```

Validate: **Developer Tools → YAML → Check Configuration**, then restart HA
core.

### Step 2 — Configure the Tailscale add-on

In **Settings → Add-ons → Tailscale → Configuration tab**, ensure the following
keys are present (merge with existing config, do not replace it):

```yaml
share_homeassistant: funnel
share_on_port: "443"
```

Save, then restart the add-on from the **Info** tab.

### Verify

The add-on log should be clean (no FATAL lines). Funnel will appear in the
Tailscale admin **Machines** view under the `homeassistant` node.

Test from any machine (on or off the tailnet):

```bash
curl -H "Authorization: Bearer <long-lived-token>" \
     https://homeassistant.tail7b513f.ts.net/api/
```

Expected: `{"message":"API running."}`

The long-lived token is generated in HA under **Profile → Long-Lived Access
Tokens**. Treat it as a secret — do not paste it into logs or shared terminals.
