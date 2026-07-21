"""Generate manifest.json for the Pico OTA update server.

Lists every firmware module ota.py should pull and overwrite on the Pico.
Deliberately excludes secrets.py/secrets.example.py — credentials are never
pushed over OTA.

Usage: python3 build_ota_manifest.py <firmware_dir>
Writes <firmware_dir>/manifest.json.
"""

import json
import sys
from pathlib import Path

_EXCLUDE = {"secrets.py", "secrets.example.py"}


def build_manifest(firmware_dir: Path) -> dict[str, list[str]]:
    files = sorted(
        p.name for p in firmware_dir.glob("*.py") if p.name not in _EXCLUDE
    )
    return {"files": files}


if __name__ == "__main__":
    firmware_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    manifest = build_manifest(firmware_dir)
    manifest_path = firmware_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {manifest_path} ({len(manifest['files'])} files)")
