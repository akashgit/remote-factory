"""Tests for the installer script and self-update CLI command."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.cli import cmd_self_update

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ── install.sh tests ─────────────────────────────────────────────


def test_install_sh_is_valid_bash():
    """install.sh passes bash -n syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_install_sh_is_executable():
    """install.sh has the executable permission bit set."""
    assert os.access(INSTALL_SH, os.X_OK), "install.sh is not executable"


def test_install_sh_has_shebang():
    """install.sh starts with a proper shebang line."""
    first_line = INSTALL_SH.read_text().splitlines()[0]
    assert first_line == "#!/usr/bin/env bash"


def test_install_sh_has_set_euo_pipefail():
    """install.sh uses strict error handling."""
    content = INSTALL_SH.read_text()
    assert "set -euo pipefail" in content


# ── self-update CLI tests ────────────────────────────────────────


def test_cmd_self_update_success():
    """cmd_self_update returns 0 when uv tool upgrade succeeds."""
    import argparse

    mock_result = subprocess.CompletedProcess(
        args=["uv", "tool", "upgrade", "remote-factory"],
        returncode=0,
        stdout="Nothing to upgrade\n",
        stderr="",
    )
    with patch("factory.cli.subprocess.run", return_value=mock_result):
        code = cmd_self_update(argparse.Namespace())
    assert code == 0


def test_cmd_self_update_failure():
    """cmd_self_update returns 1 when uv tool upgrade fails."""
    import argparse

    mock_result = subprocess.CompletedProcess(
        args=["uv", "tool", "upgrade", "remote-factory"],
        returncode=1,
        stdout="",
        stderr="error: remote-factory is not installed\n",
    )
    with patch("factory.cli.subprocess.run", return_value=mock_result):
        code = cmd_self_update(argparse.Namespace())
    assert code == 1
