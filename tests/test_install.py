"""E2E tests for the two install paths documented in README.md.

Each test installs into an isolated UV_TOOL_DIR / UV_TOOL_BIN_DIR so
nothing touches the real system.  No Docker, no network — installs
from the local checkout.

Requires `uv` on PATH.  Skipped otherwise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_uv_available = shutil.which("uv") is not None

pytestmark = [
    pytest.mark.skipif(not _uv_available, reason="uv not available"),
]


@pytest.fixture()
def isolated_tool_env(tmp_path: Path):
    """Yield env dict that redirects uv tool install to a temp directory."""
    tool_dir = tmp_path / "tools"
    bin_dir = tmp_path / "bin"
    env = os.environ.copy()
    env["UV_TOOL_DIR"] = str(tool_dir)
    env["UV_TOOL_BIN_DIR"] = str(bin_dir)
    yield env, bin_dir


class TestQuickInstall:
    """README 'Quick Install': uv tool install git+https://..."""

    def test_non_editable_install(self, isolated_tool_env):
        env, bin_dir = isolated_tool_env
        subprocess.run(
            ["uv", "tool", "install", str(REPO_ROOT)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [str(bin_dir / "factory"), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "factory" in result.stdout


class TestDevInstall:
    """README 'Development Install': git clone && uv sync && uv tool install -e ."""

    def test_editable_install(self, isolated_tool_env):
        env, bin_dir = isolated_tool_env
        subprocess.run(
            ["uv", "tool", "install", "-e", str(REPO_ROOT)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [str(bin_dir / "factory"), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "factory" in result.stdout
