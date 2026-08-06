"""Gateway GPS-RTK sensor service (gateway-sensors.service).

Polls the SparkFun GPS-RTK2 (ZED-F9P @ 0x42, I2C bus 1) every
settings.sensors_poll_interval_seconds (60s default) and publishes fix data
to MQTT. This gateway is the RTK base station reference for the survey
stick + rover fleet on the mesh.

The environmental combo board (BME280 + CCS811) previously read here has
moved to the Pico watchdog (which uses the BME280 for fan control) — the
gateway doesn't need to duplicate it.

Publishes HA MQTT discovery on startup:
  - GPS RTK status (none/float/fixed), fix type, satellite count
  - Latitude/longitude, altitude
  - Horizontal/vertical accuracy, PDOP

Topic: securitymesh/{node_id}/sensors  (retained JSON)
"""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import smbus2
import structlog

from gateway_node.config import settings
from gateway_node.gps_base_mode import (
    ACTION_ENTER_BASE,
    ACTION_EXIT_BASE,
    ACTION_START_SURVEY,
    BASE_ACTIVE,
    CMD_START_SURVEY,
    CMD_STOP_BASE,
    ROVER_NAV,
    SURVEYING,
    decide,
)
from gateway_node.gps_driver import ZedF9pDriver
from gateway_node.gps_protocol import RTK_FIXED, RTK_FLOAT, RTK_NONE, GpsFix, SvinStatus
from gateway_node.ntrip_caster import NtripCaster

log = structlog.get_logger()

_running = True
_GPS_I2C_BUS_NUMBER = 1  # /dev/i2c-1

_RTK_STATUS_LABELS = {RTK_NONE: "none", RTK_FLOAT: "float", RTK_FIXED: "fixed"}


def _cpu_temp() -> float | None:
    """Pi 5 SoC temperature — same sysfs source field-node reads on the Pi
    Zero (field_node/telemetry.py). Best-effort: None if the thermal zone
    isn't present, so a read failure never breaks the rest of the payload."""
    try:
        return float(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000.0
    except Exception:
        return None


def _gps_payload_fields(fix: GpsFix | None) -> dict[str, object]:
    if fix is None:
        return {
            "gps_fix_type": None,
            "gps_num_satellites": None,
            "gps_lat": None,
            "gps_lon": None,
            "gps_altitude_m": None,
            "gps_h_acc_m": None,
            "gps_v_acc_m": None,
            "gps_rtk_status": None,
            "gps_pdop": None,
        }
    return {
        "gps_fix_type": fix.fix_type,
        "gps_num_satellites": fix.num_satellites,
        "gps_lat": round(fix.latitude, 8),
        "gps_lon": round(fix.longitude, 8),
        "gps_altitude_m": round(fix.altitude_m, 3),
        "gps_h_acc_m": round(fix.horizontal_accuracy_m, 3),
        "gps_v_acc_m": round(fix.vertical_accuracy_m, 3),
        "gps_rtk_status": _RTK_STATUS_LABELS.get(fix.rtk_status, "none"),
        "gps_pdop": round(fix.pdop, 2),
    }


def _base_payload_fields(state: str, svin: SvinStatus | None) -> dict[str, object]:
    return {
        "gps_base_state": state,
        "gps_svin_duration_s": svin.dur_s if svin is not None else None,
        "gps_svin_meanacc_m": round(svin.mean_acc_m, 3) if svin is not None else None,
    }


def _is_fix_stale(age_s: float, timeout_s: float) -> bool:
    """True once a fix is old enough to stop trusting — see
    Settings.gps_fix_stale_timeout_s for why this exists."""
    return age_s > timeout_s


def _handle_signal(signum: int, frame: object) -> None:
    global _running
    _running = False


def _gateway_device() -> dict[str, object]:
    return {
        "identifiers": [settings.node_id],
        "name": "SFV Gateway",
        "model": "Pi 5 Gateway Node",
        "manufacturer": "SmartFarmView",
    }


def _publish_discovery(client: mqtt.Client) -> None:
    node = settings.node_id
    prefix = settings.mqtt_discovery_prefix
    state_topic = f"securitymesh/{node}/sensors"
    device = _gateway_device()

    entities: list[tuple[str, dict[str, object]]] = [
        (
            "gps_rtk_status",
            {
                "name": "GPS RTK Status",
                "unique_id": f"{node}_gps_rtk_status",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_rtk_status }}",
                "icon": "mdi:crosshairs-gps",
                "device": device,
            },
        ),
        (
            "gps_fix_type",
            {
                "name": "GPS Fix Type",
                "unique_id": f"{node}_gps_fix_type",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_fix_type }}",
                "icon": "mdi:satellite-variant",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "gps_num_satellites",
            {
                "name": "GPS Satellites",
                "unique_id": f"{node}_gps_num_satellites",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_num_satellites }}",
                "state_class": "measurement",
                "icon": "mdi:satellite-variant",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "gps_lat",
            {
                "name": "GPS Latitude",
                "unique_id": f"{node}_gps_lat",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_lat }}",
                "icon": "mdi:map-marker",
                "device": device,
            },
        ),
        (
            "gps_lon",
            {
                "name": "GPS Longitude",
                "unique_id": f"{node}_gps_lon",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_lon }}",
                "icon": "mdi:map-marker",
                "device": device,
            },
        ),
        (
            "gps_altitude_m",
            {
                "name": "GPS Altitude",
                "unique_id": f"{node}_gps_altitude_m",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_altitude_m }}",
                "unit_of_measurement": "m",
                "device_class": "distance",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "gps_h_acc_m",
            {
                "name": "GPS Horizontal Accuracy",
                "unique_id": f"{node}_gps_h_acc_m",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_h_acc_m }}",
                "unit_of_measurement": "m",
                "device_class": "distance",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "icon": "mdi:target",
                "device": device,
            },
        ),
        (
            "gps_v_acc_m",
            {
                "name": "GPS Vertical Accuracy",
                "unique_id": f"{node}_gps_v_acc_m",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_v_acc_m }}",
                "unit_of_measurement": "m",
                "device_class": "distance",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "icon": "mdi:target",
                "device": device,
            },
        ),
        (
            "gps_pdop",
            {
                "name": "GPS PDOP",
                "unique_id": f"{node}_gps_pdop",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_pdop }}",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "icon": "mdi:target",
                "device": device,
            },
        ),
        (
            "cpu_temp",
            {
                "name": "CPU Temperature",
                "unique_id": f"{node}_cpu_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.cpu_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "device": device,
            },
        ),
        (
            "gps_base_state",
            {
                "name": "GPS Base State",
                "unique_id": f"{node}_gps_base_state",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_base_state }}",
                "icon": "mdi:radio-tower",
                "device": device,
            },
        ),
        (
            "gps_svin_duration",
            {
                "name": "GPS Survey-In Duration",
                "unique_id": f"{node}_gps_svin_duration",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_svin_duration_s }}",
                "unit_of_measurement": "s",
                "device_class": "duration",
                "state_class": "measurement",
                "icon": "mdi:timer-sand",
                "device": device,
            },
        ),
        (
            "gps_svin_meanacc",
            {
                "name": "GPS Survey-In Accuracy",
                "unique_id": f"{node}_gps_svin_meanacc",
                "state_topic": state_topic,
                "value_template": "{{ value_json.gps_svin_meanacc_m }}",
                "unit_of_measurement": "m",
                "device_class": "distance",
                "state_class": "measurement",
                "icon": "mdi:target",
                "device": device,
            },
        ),
    ]

    for object_id, config in entities:
        topic = f"{prefix}/sensor/{node}/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)
        log.info("discovery_published", object_id=object_id)

    cmd_topic = f"securitymesh/{node}/cmd"
    buttons: list[tuple[str, dict[str, object]]] = [
        (
            "gps_start_survey",
            {
                "name": "GPS Start Survey",
                "unique_id": f"{node}_gps_start_survey",
                "command_topic": cmd_topic,
                "payload_press": json.dumps({"cmd": CMD_START_SURVEY}),
                "icon": "mdi:crosshairs-gps",
                "device": device,
            },
        ),
        (
            "gps_stop_base",
            {
                "name": "GPS Stop Base",
                "unique_id": f"{node}_gps_stop_base",
                "command_topic": cmd_topic,
                "payload_press": json.dumps({"cmd": CMD_STOP_BASE}),
                "icon": "mdi:stop-circle-outline",
                "device": device,
            },
        ),
    ]
    for object_id, config in buttons:
        topic = f"{prefix}/button/{node}/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)
        log.info("discovery_published", object_id=object_id)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── GPS ──────────────────────────────────────────────────────────────────
    gps: ZedF9pDriver | None = None
    if settings.gps_enabled:
        try:
            gps_bus = smbus2.SMBus(_GPS_I2C_BUS_NUMBER)
            gps = ZedF9pDriver(gps_bus, addr=settings.gps_i2c_addr)
            log.info("gps_initialised", addr=hex(settings.gps_i2c_addr))
        except Exception as e:  # noqa: BLE001 — a GPS hiccup shouldn't crash the service
            log.warning("gps_init_failed", error=str(e))
            gps = None
    last_gps_fix: GpsFix | None = None
    last_gps_fix_monotonic = 0.0

    # RTK base-station mode (see gps_base_mode.py). base_state governs which
    # of NAV-PVT/NAV-SVIN/RTCM3 is currently enabled on I2C — never more than
    # one at once, see gps_driver.py.
    base_state = ROVER_NAV
    last_svin: SvinStatus | None = None
    pending_cmd: str | None = None

    caster: NtripCaster | None = None
    if gps is not None:
        caster = NtripCaster(settings.rtk_caster_port, settings.rtk_mountpoint)
        caster.start()

    # ── MQTT ─────────────────────────────────────────────────────────────────
    client: mqtt.Client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
        client_id=f"{settings.node_id}-sensors",
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    cmd_topic = f"securitymesh/{settings.node_id}/cmd"

    def on_connect(
        c: mqtt.Client, userdata: object, flags: object, rc: object, props: object = None
    ) -> None:
        log.info("mqtt_connected")
        c.subscribe(cmd_topic)
        _publish_discovery(c)

    def on_message(c: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
        nonlocal pending_cmd
        if msg.topic != cmd_topic:
            return
        try:
            data = json.loads(msg.payload.decode())
        except Exception as e:
            log.warning("cmd_parse_error", error=str(e))
            return
        cmd = data.get("cmd")
        # This topic is shared with gateway-power's capture/prepare_shutdown
        # commands (multiple listeners on one topic is an established
        # pattern here) — only react to the two we own.
        if cmd in (CMD_START_SURVEY, CMD_STOP_BASE):
            pending_cmd = cmd
            log.info("gps_base_command_received", cmd=cmd)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    client.loop_start()

    state_topic = f"securitymesh/{settings.node_id}/sensors"
    last_poll = 0.0

    try:
        while _running:
            now = time.monotonic()

            # Drain whatever the current base_state has enabled on I2C every
            # loop tick (~1s), independent of the publish cadence below. The
            # ZED-F9P's I2C output buffer is small and fills within seconds,
            # so waiting a full sensors_poll_interval_seconds (60s default)
            # between drains lets far more back up than the buffer holds,
            # overflowing it and corrupting/dropping frames long before
            # anything looks like an I2C error. Confirmed against a
            # standalone script that drained every ~0.2s and never dropped a
            # fix in 175s straight, versus this service (draining only once
            # a minute) rarely holding one.
            if gps is not None:
                if base_state == ROVER_NAV:
                    fix = gps.drain_latest_fix()
                    if fix is not None:
                        last_gps_fix = fix
                        last_gps_fix_monotonic = now
                elif base_state == SURVEYING:
                    svin = gps.poll_svin_status()
                    if svin is not None:
                        last_svin = svin
                elif base_state == BASE_ACTIVE:
                    rtcm = gps.drain_rtcm3()
                    if rtcm and caster is not None:
                        caster.feed(rtcm)

                new_state, action = decide(base_state, pending_cmd, last_svin)
                pending_cmd = None
                if action == ACTION_START_SURVEY:
                    gps.enter_survey_in(settings.gps_svin_min_dur_s, settings.gps_svin_acc_limit_m)
                    last_gps_fix = None
                    last_svin = None
                elif action == ACTION_ENTER_BASE:
                    gps.enter_base_active()
                elif action == ACTION_EXIT_BASE:
                    gps.exit_base_mode()
                    last_svin = None
                if new_state != base_state:
                    log.info("gps_base_state_changed", old=base_state, new=new_state, action=action)
                base_state = new_state

            if now - last_poll >= settings.sensors_poll_interval_seconds:
                if last_gps_fix is not None and _is_fix_stale(
                    now - last_gps_fix_monotonic, settings.gps_fix_stale_timeout_s
                ):
                    log.warning(
                        "gps_fix_stale_dropping",
                        age_s=round(now - last_gps_fix_monotonic, 1),
                    )
                    last_gps_fix = None
                try:
                    payload: dict[str, object] = _gps_payload_fields(last_gps_fix)
                    payload.update(_base_payload_fields(base_state, last_svin))
                    cpu_temp = _cpu_temp()
                    payload["cpu_temp"] = round(cpu_temp, 1) if cpu_temp is not None else None
                    client.publish(state_topic, json.dumps(payload), retain=True)
                    log.info(
                        "sensors_published",
                        gps_rtk_status=payload["gps_rtk_status"],
                        gps_num_satellites=payload["gps_num_satellites"],
                        gps_base_state=payload["gps_base_state"],
                        cpu_temp=payload["cpu_temp"],
                    )
                except Exception as e:
                    log.warning("sensor_read_error", error=str(e))
                last_poll = now

            time.sleep(1)
    finally:
        if caster is not None:
            caster.stop()
        client.loop_stop()
        client.disconnect()
        log.info("sensors_service_stopped")
