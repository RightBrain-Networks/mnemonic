"""Fresh installation settings derive identity only from explicitly chosen sources."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def setup_checkout(tmp_path):
    script = tmp_path / "scripts" / "setup.py"
    script.parent.mkdir()
    script.write_bytes((ROOT / "scripts/setup.py").read_bytes())
    (tmp_path / ".env.example").write_bytes((ROOT / ".env.example").read_bytes())
    return script


@pytest.mark.skipif(os.geteuid() == 0, reason="service identities must be unprivileged")
def test_explicit_source_selects_owner_without_widening_roots(tmp_path):
    script = setup_checkout(tmp_path)
    source = tmp_path / "private-source"
    source.mkdir(mode=0o700)
    result = subprocess.run([sys.executable, str(script), "--transcript-source", str(source)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    env = (tmp_path / ".env").read_text()
    assert f"MNEMONIC_API_UID={os.geteuid()}" in env
    assert f"MNEMONIC_API_GID={os.getegid()}" in env
    assert f"MNEMONIC_TRANSCRIPT_SOURCE_DIR='{source}'" in env
    assert "MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS=[]" in env
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    assert "--one-off" in result.stdout
    # Existing secrets/configuration are never silently changed.
    before = (tmp_path / ".env").read_bytes()
    repeated = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert repeated.returncode == 0
    assert (tmp_path / ".env").read_bytes() == before


def test_setup_rejects_missing_and_symlink_roots(tmp_path):
    script = setup_checkout(tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(target)
    for source in (link, tmp_path / "missing"):
        result = subprocess.run([sys.executable, str(script), "--transcript-source", str(source)],
                                capture_output=True, text=True)
        assert result.returncode != 0
        assert not (tmp_path / ".env").exists()
