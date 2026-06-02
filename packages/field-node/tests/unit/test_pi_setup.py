"""Idempotency contract for scripts/setup-ha-mount.sh.

Runs the script with SKIP_PRIV=1 (no apt/mount/chmod/root) and SUDO="" against
temp fstab + .env files, and asserts that a second run does not duplicate the
fstab entry and never overwrites a pre-existing FIELD_NODE_DETECTION_STORE_DIR.
No root or cifs needed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup-ha-mount.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _invoke(fstab: Path, env_file: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "FSTAB": str(fstab),
        "ENV_FILE": str(env_file),
        "CREDS_FILE": str(tmp_path / "creds"),
        "MNT_BASE": str(tmp_path / "mnt"),
        "SUDO": "",
        "SKIP_PRIV": "1",
        "HOSTN": "testnode",
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def test_fstab_entry_added_once_then_not_duplicated(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    fstab.write_text("# existing entries\n")
    env_file = tmp_path / ".env"
    env_file.write_text("FIELD_NODE_HA_SMB_HOST=gw.example.ts.net\n")

    _invoke(fstab, env_file, tmp_path)
    _invoke(fstab, env_file, tmp_path)  # second run must not duplicate

    cifs_lines = [ln for ln in fstab.read_text().splitlines() if "cifs" in ln]
    assert len(cifs_lines) == 1
    # Share root is mounted at MNT_BASE itself (not a per-host subdir).
    assert f" {tmp_path / 'mnt'} " in cifs_lines[0]


def test_store_dir_set_when_absent(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("FIELD_NODE_HA_SMB_HOST=gw.example.ts.net\n")

    _invoke(fstab, env_file, tmp_path)

    expected = tmp_path / "mnt" / "landplan" / "testnode"
    content = env_file.read_text()
    assert f"FIELD_NODE_DETECTION_STORE_DIR={expected}" in content


def test_pre_existing_store_dir_not_overwritten(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FIELD_NODE_HA_SMB_HOST=gw.example.ts.net\n"
        "FIELD_NODE_DETECTION_STORE_DIR=/preexisting/path\n"
    )

    _invoke(fstab, env_file, tmp_path)
    _invoke(fstab, env_file, tmp_path)

    content = env_file.read_text()
    assert "FIELD_NODE_DETECTION_STORE_DIR=/preexisting/path" in content
    assert content.count("FIELD_NODE_DETECTION_STORE_DIR=") == 1


def test_skips_silently_when_ha_host_unset(tmp_path: Path) -> None:
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("# no smb host configured\n")

    result = _invoke(fstab, env_file, tmp_path)

    assert "cifs" not in fstab.read_text()
    assert "FIELD_NODE_DETECTION_STORE_DIR" not in env_file.read_text()
    assert "skipping HA media mount" in result.stdout
