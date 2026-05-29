#!/usr/bin/env python3
"""
Standalone HC-SR501 PIR sensor test script.

Run on the Pi to verify wiring before starting the full field-node service:

    python3 scripts/test_pir.py
    python3 scripts/test_pir.py --pin 26

Prints a status line every second and logs motion start/clear events.
Ctrl+C to exit.
"""
import argparse
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Test HC-SR501 PIR wiring")
    parser.add_argument(
        "--pin",
        type=int,
        default=26,
        help="BCM GPIO pin number (default: 26)",
    )
    args = parser.parse_args()

    try:
        from field_node.motion import PIRSensor
    except ImportError:
        # Allow running directly from the scripts/ directory without a venv
        sys.path.insert(0, "src")
        from field_node.motion import PIRSensor

    from field_node.config import settings

    warmup = settings.pir_warmup_seconds
    print(f"PIR test — GPIO {args.pin} (BCM), physical pin 37")
    print(f"Warming up for {warmup}s … (sensor output is suppressed during this window)")
    print("Press Ctrl+C to exit.\n")

    pir = PIRSensor(pin=args.pin)

    def on_motion() -> None:
        print(f"\n[{time.strftime('%H:%M:%S')}]  *** MOTION DETECTED ***")

    def on_clear() -> None:
        print(f"\n[{time.strftime('%H:%M:%S')}]      motion cleared")

    pir.on_motion = on_motion
    pir.on_clear = on_clear

    try:
        while True:
            if pir.is_warming_up:
                status = "warming up …"
            elif pir.is_detected:
                status = "MOTION"
            else:
                status = "clear"
            print(f"\r[{time.strftime('%H:%M:%S')}]  {status:<20}", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        pir.close()


if __name__ == "__main__":
    main()
