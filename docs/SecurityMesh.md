
## Overview

SecurityMesh is a distributed, solar-powered, Raspberry Pi–based wireless mesh network designed for:

- Remote property security
    
- Off-grid monitoring
    
- Wildlife observation
    
- Infrastructure telemetry
    
- Environmental sensing
    
- RTK GPS correction broadcasting
    
- Local-first autonomous operation
    

The system is intended to operate reliably in rural or off-grid environments with limited or intermittent internet connectivity, initially using a cellular uplink and later expanding to Starlink connectivity.

Every deployed node acts simultaneously as:

- A security camera
    
- A motion detection device
    
- A wireless mesh participant
    
- A telemetry sensor
    
- A local compute node
    

This creates a resilient and scalable distributed monitoring system.

---

# Design Goals

## Primary Goals

- Operate fully off-grid using solar and battery power
    
- Continue functioning during internet outages
    
- Minimize bandwidth usage over cellular connections
    
- Provide distributed camera coverage across large properties
    
- Support autonomous local operation
    
- Integrate tightly with Home Assistant
    
- Support RTK GPS correction broadcasting
    
- Use low-cost DIY standardized hardware
    

---

# Core Principles

## Local-First Operation

Nodes should continue operating even if:

- cellular connectivity fails
    
- Starlink is offline
    
- gateway internet access is unavailable
    

All recording, motion detection, and event generation occur locally at the node level.

---

## Event-Based Recording

The system is optimized for:

- motion-triggered capture
    
- event clips
    
- snapshots
    
- delayed synchronization
    

The system intentionally avoids:

- continuous live video streaming
    
- high-bandwidth remote camera feeds
    

---

## Mesh Networking

Every node participates in the wireless mesh network, improving:

- redundancy
    
- network range
    
- resilience
    
- self-healing capabilities
    

---

## Standardized Node Design

All deployed field nodes use the same core architecture and software stack to simplify:

- maintenance
    
- replacement
    
- software deployment
    
- troubleshooting
    
- future upgrades
    

---

# System Architecture

## Gateway Node

The gateway node is the central infrastructure controller and uplink point.

### Responsibilities

- Home Assistant server
    
- MQTT broker
    
- Mesh routing coordination
    
- Local NVR and storage
    
- Cellular and/or Starlink uplink
    
- VPN access
    
- OTA update coordination
    
- RTK correction broadcasting
    
- GPS timing source
    
- Local dashboards and automations
    

---

## Gateway Hardware

Recommended hardware:

- Raspberry Pi 5 or CM4
    
- External SSD storage
    
- High-gain cellular antenna
    
- LTE modem/router
    
- Optional Starlink terminal
    
- Large solar array
    
- Large LiFePO4 battery bank
    
- GPS receiver module connected over I2C
    
- Optional LoRa radio
    

> **Concrete design:** see [`gateway-node.md`](gateway-node.md) for the
> implemented gateway — Raspberry Pi 5, EcoWorthy 100W/PWM/20Ah LiFePO4 baseline,
> a **Raspberry Pi Pico 2 W power controller** for graceful shutdown +
> wake-on-recharge + hardware watchdog ([`pico-watchdog.md`](pico-watchdog.md)),
> and a **Waveshare SIM7600G-H DTU** providing the cellular uplink *and* the GNSS
> timing source below. Power management is two-tier: a software brain
> (`packages/gateway-node`, sharing `packages/power-policy` with field nodes)
> degrades gracefully; the Pico is the autonomous hardware backstop.

---

# GPS / RTK Integration

The gateway node includes a high-quality GPS receiver connected via I2C.

## GPS Functions

- Accurate system time source
    
- Geographic reference location
    
- RTK base station support
    
- NTP stratum source
    
- Mesh-wide time synchronization
    

---

## RTK Correction Broadcasting

The gateway broadcasts RTK correction data for centimeter-accuracy surveying projects.

Possible transport methods:

- NTRIP server
    
- Local mesh multicast
    
- WiFi distribution
    
- LoRa fallback telemetry
    

This enables compatible RTK rover devices across the property to achieve high-accuracy positioning.

---

# Field Node Architecture

Each field node is a standardized autonomous edge device.

Every node includes:

- Raspberry Pi Zero 2 W
    
- Pi Camera
    
- PIR motion detector
    
- Solar charging system
    
- LiFePO4 battery
    
- Wireless mesh participation
    
- Local storage
    
- Environmental telemetry
    

---

# Node Responsibilities

Each node simultaneously acts as:

- Camera device
    
- Motion sensor
    
- Mesh relay
    
- Telemetry publisher
    
- Local recording device
    

---

# Standard Node Hardware

## Compute

- Raspberry Pi Zero 2 W
    

---

## Camera

- Raspberry Pi Camera Module 3
    

Capabilities:

- 1080p recording
    
- hardware H.264 encoding
    
- low-light support
    

---

## Motion Detection

- PIR motion sensor
    

Used for:

- low-power event triggering
    
- reducing unnecessary CPU usage
    
- reducing false positives
    

---

## Power System

Recommended:

- LiFePO4 battery
    
- MPPT or solar charge controller
    
- 25–100W solar panel depending on deployment
    

---

## Storage

- Industrial-grade microSD
    
- Optional external SSD on larger nodes
    

Nodes maintain:

- rolling local video buffer
    
- event archives
    
- telemetry logs
    

---

## Wireless

Primary:

- onboard WiFi mesh participation
    

Optional:

- external directional radios
    
- LoRa telemetry modules
    

---

# Mesh Networking

## Recommended Stack

- BATMAN-adv mesh networking
    
- hostapd
    
- dnsmasq
    
- WireGuard or Tailscale
    

---

## Mesh Characteristics

The network should provide:

- self-healing routes
    
- decentralized communication
    
- multi-hop routing
    
- local-only operation without internet
    

---

# Home Assistant Integration

The system is designed to integrate tightly with Home Assistant.

## Gateway Services

The gateway node runs:

- Home Assistant
    
- Mosquitto MQTT broker
    
- Frigate (optional)
    
- Grafana (optional)
    
- Node-RED (optional)
    

---

# MQTT Telemetry

Each node publishes telemetry such as:

- battery voltage
    
- solar charging state
    
- CPU temperature
    
- signal strength
    
- storage usage
    
- motion events
    
- camera health
    

Example topics:

```text
securitymesh/node01/battery
securitymesh/node01/motion
securitymesh/node01/storage
securitymesh/node01/signal
```

---

# Camera Workflow

## Idle State

Nodes:

- participate in mesh routing
    
- publish telemetry
    
- monitor PIR sensor
    
- remain in low-power operation
    

---

## Motion Event Workflow

1. PIR detects motion
    
2. Node activates recording pipeline
    
3. Pre-buffered clip is saved locally
    
4. Snapshot generated
    
5. MQTT event published
    
6. Home Assistant automation triggered
    
7. Full clip synchronized later if bandwidth allows
    

---

# Bandwidth Strategy

The system is optimized for low-bandwidth environments.

## Preferred Workflow

- store high-quality footage locally
    
- upload thumbnails immediately
    
- upload clips asynchronously
    
- delay large synchronization jobs until:
    
    - nighttime
        
    - strong solar conditions
        
    - Starlink availability
        

---

# AI / Object Detection

Future expansion may include:

- person detection
    
- vehicle detection
    
- wildlife classification
    

Recommended architecture:

## Edge Nodes

Perform:

- lightweight motion detection
    
- event capture
    

## Gateway Node

Performs:

- AI inference
    
- Frigate object classification
    
- centralized indexing
    

---

# Reliability Features

The system prioritizes remote reliability.

## Recommended Features

- read-only root filesystem
    
- watchdog auto-reboot
    
- local buffering
    
- heartbeat monitoring
    
- OTA updates
    
- battery telemetry
    
- automatic recovery services
    

---

# Environmental Hardening

Enclosures should be:

- weatherproof
    
- ventilated
    
- insect resistant
    
- UV resistant
    

Recommended additions:

- conformal coating
    
- cable glands
    
- desiccant packs
    

---

# Future Expansion

Possible future additions:

- LoRa fallback network
    
- environmental sensors
    
- weather stations
    
- acoustic monitoring
    
- distributed AI inference
    
- autonomous drones
    
- smart lighting
    
- remote sirens
    
- edge ML acceleration
    

---

# Philosophy

SecurityMesh is designed as:

> A distributed autonomous edge-computing infrastructure for remote land stewardship and monitoring.

It is intentionally:

- modular
    
- decentralized
    
- solar friendly
    
- bandwidth efficient
    
- repairable
    
- DIY-friendly
    
- Home Assistant native
    

rather than a traditional cloud-dependent security camera system.