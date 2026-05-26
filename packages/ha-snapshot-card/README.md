# SmartFarmView Snapshot Card

A Lovelace card for Home Assistant that displays the most recent JPEG snapshot from a SecurityMesh field node, with capture timestamp, age indicator, and an on-demand capture button.

## Features

- Shows the latest snapshot image from an MQTT camera entity
- "Captured X minutes ago" label, refreshed every 30 seconds
- **Capture** button triggers the field node to take a new photo immediately
- Button disables for 5 seconds after press to prevent spamming
- Adapts to HA theme colours (dark/light)

## Installation

### Manual (development)

1. Copy `src/smartfarmview-snapshot-card.js` to your HA `config/www/` directory
2. In HA → Settings → Dashboards → Resources, add:
   - URL: `/local/smartfarmview-snapshot-card.js`
   - Type: JavaScript module
3. Clear browser cache and reload

### HACS

> HACS distribution requires extracting this card to its own repository. This is planned for a future release once the card stabilises.

## Card configuration

```yaml
type: custom:smartfarmview-snapshot-card
camera_entity: camera.landplanmesh1_camera
button_entity: button.landplanmesh1_capture_snapshot
```

| Option | Required | Description |
|---|---|---|
| `camera_entity` | Yes | The HA MQTT camera entity for this node |
| `button_entity` | Yes | The HA button entity that triggers capture |
| `title` | No | Optional card title |

## How the image gets to HA

The field node captures a JPEG via picamera2 and publishes the raw bytes to an MQTT topic with `retain=True`. HA's MQTT camera integration subscribes to that topic and serves the image via its camera proxy API. This card reads directly from the camera proxy — no polling, no file storage on HA.
