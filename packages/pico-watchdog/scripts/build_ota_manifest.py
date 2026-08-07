"""Generate manifest.json (and stamp firmware_version.py) for the Pico OTA
update server.

Lists every firmware module ota.py should pull and overwrite on the Pico.
Deliberately excludes secrets.py/secrets.example.py — credentials are never
pushed over OTA.

Usage: python3 build_ota_manifest.py <firmware_dir>
Writes <firmware_dir>/manifest.json and overwrites
<firmware_dir>/firmware_version.py's VERSION with the current git short SHA
(+"-dirty" if there are uncommitted changes), so every OTA is traceable to
an exact commit without a separate manual version bump to remember.
"""

import json
import subprocess
import sys
from pathlib import Path

_EXCLUDE = {"secrets.py", "secrets.example.py"}


def build_manifest(firmware_dir: Path) -> dict[str, list[str]]:
    files = sorted(p.name for p in firmware_dir.glob("*.py") if p.name not in _EXCLUDE)
    return {"files": files}


def format_version(short_sha: str, dirty: bool) -> str:
    return f"{short_sha}-dirty" if dirty else short_sha


def git_version(repo_dir: Path) -> str:
    """Return format_version()'s result for the current git state, or
    "unknown" if this isn't a git checkout (e.g. an extracted tarball)."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return format_version(sha, dirty=bool(status.strip()))


def write_version_file(firmware_dir: Path, version: str) -> None:
    version_path = firmware_dir / "firmware_version.py"
    version_path.write_text(f'VERSION = "{version}"\n')


if __name__ == "__main__":
    firmware_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

    version = git_version(firmware_dir)
    write_version_file(firmware_dir, version)
    print(f"stamped firmware_version.py: {version}")

    manifest = build_manifest(firmware_dir)
    manifest_path = firmware_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {manifest_path} ({len(manifest['files'])} files)")
