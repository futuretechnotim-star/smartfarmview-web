# Agent Loop Ready: SmartFarmView Web

Date: 2026-06-07

Claude Code has set up the .agents/handoffs/ coordination pattern for this repo.

## Project Context For Codex

SmartFarmView is a mesh-network farm observability platform. Monorepo containing
a web frontend and multiple Python device packages that run on Raspberry Pi nodes
communicating over a TailScale mesh network. Pi Zero 2 W field nodes capture
camera/motion/telemetry; a Pi 5 gateway acts as the power brain and MQTT/mesh
host; a Pico 2 W watchdog controls power.

## Repo

https://github.com/futuretechnotim-star/smartfarmview-web

## Stack Notes

- Web frontend: apps/web/ (framework TBD)
- Python device packages: packages/field-node, packages/gateway-node, packages/pico-watchdog, packages/power-policy
- Networking: TailScale mesh
- Node registry: infra/nodes.json
- Pi devices pull updates via GitHub Actions (not VS Code Remote SSH)

## /loop Command For Claude Sessions

/loop Check `.agents/handoffs/` in `/Users/timwebster/Documents/code/smartfarmview-web` for any new `*request*` handoff files that don't yet have a corresponding `*return*` handoff. If a new request is found, read it fully and implement the work it describes — then write a return handoff to `.agents/handoffs/`. If no new requests are found, do nothing and wait for the next check.
