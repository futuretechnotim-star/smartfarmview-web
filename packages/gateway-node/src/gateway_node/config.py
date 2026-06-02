import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_NODE_", env_file=".env", extra="ignore")

    # Node identity
    node_id: str = socket.gethostname()

    # MQTT — the broker runs on this gateway; the power brain connects locally.
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_discovery_prefix: str = "homeassistant"

    # Topic the Pico 2 W watchdog publishes battery/solar telemetry on.
    # Expected JSON payload: {"soc_pct": int, "current_ma": float, "voltage_v": float, ...}
    # current_ma: positive = charging, negative = discharging (battery supplying load).
    pico_telemetry_topic: str = "securitymesh/gateway/pico/telemetry"

    # Battery capacity in mAh — EcoWorthy 12.8V 20Ah LiFePO4 = 20000 mAh.
    battery_capacity_mah: int = 20000

    # Solar-aware power management (matches field-node semantics).
    solar_day_start_hour: int = 7
    solar_day_end_hour: int = 20
    solar_min_overnight_soc: int = 40  # gateway carries more services → larger reserve
    solar_current_avg_minutes: int = 30

    # Heartbeat published to the Pico so its hardware watchdog knows the Pi is alive.
    heartbeat_topic: str = "securitymesh/gateway/pi/heartbeat"
    heartbeat_interval_seconds: int = 30

    # Services (systemd units or `docker compose` service names) stopped per mode,
    # most-aggressive last. Comma-separated in env, e.g.
    #   GATEWAY_NODE_ECO_STOP="frigate"
    #   GATEWAY_NODE_LOW_STOP="frigate,grafana"
    #   GATEWAY_NODE_CRITICAL_STOP="frigate,grafana,gateway-camera"
    # Empty by default so the brain is observe-only until the operator opts in.
    eco_stop: str = ""
    low_stop: str = ""
    critical_stop: str = ""

    # How services are controlled. "systemd" → systemctl; "compose" → docker compose;
    # "dry-run" → log intended actions only (safe default for the baseline phase).
    service_control: str = "dry-run"
    compose_file: str = "/opt/gateway-node/docker-compose.yml"

    def stop_list(self, key: str) -> list[str]:
        raw = {"eco": self.eco_stop, "low": self.low_stop, "critical": self.critical_stop}[key]
        return [s.strip() for s in raw.split(",") if s.strip()]


settings = Settings()
