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

    # Camera
    capture_width: int = 1920
    capture_height: int = 1080
    capture_dir: str = "/opt/field-node/captures"

    # Telemetry
    telemetry_interval_seconds: int = 60

    # Power monitor driver — swap to change HAT (see docs/power-hat.md)
    power_monitor: str = "ina219_hat"

    # Battery capacity in mAh — update when swapping batteries
    battery_capacity_mah: int = 1500

    # PIR GPIO pin (BCM numbering) — not yet wired, reserved for future use
    pir_gpio_pin: int = 17


settings = Settings()
