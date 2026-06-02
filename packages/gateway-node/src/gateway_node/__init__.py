"""SecurityMesh Gateway Node.

The gateway hosts Home Assistant, the Mosquitto MQTT broker, BATMAN-adv mesh
coordination, an event-capture camera, and the internet uplink. This package
provides the gateway's **power-budget brain**: it consumes battery/solar
telemetry published over MQTT by the Pico 2 W watchdog (which reads the solar
charge controller over RS485), runs the shared NORMAL/ECO/LOW/CRITICAL policy,
and gracefully throttles or stops services to stay within the daily solar
budget. The Pico is the independent hardware backstop that enforces shutdown and
owns wake-on-recharge — see docs/gateway-node.md and docs/pico-watchdog.md.
"""
