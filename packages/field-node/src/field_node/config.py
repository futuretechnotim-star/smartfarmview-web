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

    # Camera — DORHEA B0036 160° FOV 5MP fisheye (CSI, OV5647)
    # Native sensor resolution is 2592x1944 (4:3); fisheye fills the full frame at this ratio
    capture_width: int = 2592
    capture_height: int = 1944
    capture_dir: str = "/opt/field-node/captures"
    # 15fps keeps the OV5647 exposure window long enough for correct colour balance;
    # higher framerates (≥30fps) force short exposures that cause a pink colour cast
    camera_framerate: int = 15

    # Telemetry
    telemetry_interval_seconds: int = 60

    # Power monitor driver — swap to change HAT (see docs/power-hat.md)
    power_monitor: str = "ina219_hat"

    # Battery capacity in mAh — update when swapping batteries
    battery_capacity_mah: int = 1500

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

    # Solar-aware power management
    solar_day_start_hour: int = 7  # local 24h hour when solar generation begins
    solar_day_end_hour: int = 20  # local 24h hour when solar generation ends
    solar_min_overnight_soc: int = 30  # minimum SoC % needed at day_end to run through the night
    solar_current_avg_minutes: int = 30  # rolling window for net current average


settings = Settings()
