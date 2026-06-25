import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIELD_NODE_", env_file=".env", extra="ignore")

    # Node identity
    node_id: str = socket.gethostname()

    # MQTT
    mqtt_host: str = "gateway.local"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_discovery_prefix: str = "homeassistant"

    # Camera — Arducam 16MP IMX519, 120° (D) M12 wide-angle lens (CSI)
    # IMX519 native modes (4:3): 1280x720, 1920x1080, 2328x1748, 3840x2160, 4656x3496.
    # 2328x1748 (2x2 binned) balances detail, low-light, and file/encode size for
    # solar-budgeted snapshot capture; raise to 4656x3496 for full 16MP stills.
    # Requires dtoverlay=imx519 in config.txt (handled by pi-setup.sh) — the IMX519
    # is not auto-detected by libcamera.
    capture_width: int = 2328
    capture_height: int = 1748
    capture_dir: str = "/opt/field-node/captures"
    # IMX519 has no OV5647 short-exposure colour-cast issue, so framerate is not
    # constrained for colour balance; 30fps is a sane default for AE settling.
    camera_framerate: int = 30

    # Telemetry
    telemetry_interval_seconds: int = 60

    # Power monitor driver — swap to change HAT (see docs/power-hat.md)
    power_monitor: str = "ina219_hat"

    # Battery capacity in mAh — update when swapping batteries
    battery_capacity_mah: int = 10000

    # PIR sensor (HC-SR501) — GPIO pin in BCM numbering, physical pin 37
    pir_gpio_pin: int = 26
    pir_warmup_seconds: int = 60
    # gpiozero SmoothedInputDevice queue depth — higher = less noise, more latency
    pir_queue_len: int = 5
    # pin must stay HIGH this long before on_motion fires (filters sub-second glitches)
    pir_min_duration_seconds: float = 2.0
    # minimum gap between consecutive on_motion callbacks (prevents rapid re-triggering)
    pir_cooldown_seconds: float = 10.0

    # Object detector (TFLite COCO SSD MobileNet V1)
    detector_model_path: str = "/opt/field-node/models/detect.tflite"
    detector_labels_path: str = "/opt/field-node/models/labelmap.txt"
    detector_min_confidence: float = 0.5  # 0.0–1.0; raise to reduce false positives

    # Detection history — hi-res image store.
    # detection_store_dir: where detection JPEGs are written. pi-setup.sh sets this
    # automatically (in /opt/field-node/.env) to /mnt/ha-media/landplan/<host> when
    # ha_smb_host is configured (Samba `media` share mounted at /mnt/ha-media).
    # HA then serves them at {haBaseUrl}/media/local/landplan/<host>/{id}.jpg (Bearer auth).
    # imageFilename in detection events stores the full HA-media-relative path so the
    # LandPlan API proxy doesn't need to reconstruct it.
    # Empty = disabled.
    detection_store_dir: str = ""
    # Number of detection events (and corresponding images) to keep.
    # Oldest files are deleted when the limit is reached.
    detection_store_count: int = 10

    # Tailscale hostname/IP of the HA server exposing the `media` Samba share,
    # e.g. "gateway1.tailnet-xxxx.ts.net". Consumed by pi-setup.sh (not the Python
    # runtime) to mount the share. Empty → detection image storage stays disabled.
    ha_smb_host: str = ""

    # Solar-aware power management
    solar_day_start_hour: int = 7  # local 24h hour when solar generation begins
    solar_day_end_hour: int = 20  # local 24h hour when solar generation ends
    solar_min_overnight_soc: int = 55  # minimum SoC % needed at day_end to run through the night
    solar_current_avg_minutes: int = 60  # rolling window for net current average

    # Dawn recovery — if dawn SoC is below this, hold LOW until battery recovers
    dawn_low_soc_threshold: int = 50
    dawn_recovery_soc: int = 65

    # CRITICAL mode periodic capture — interval in seconds between forced check-in
    # captures (with object detection) while motion-triggered captures are suppressed.
    # 3600 = one capture per hour.
    critical_capture_interval_s: int = 3600


settings = Settings()
