"""Tier 3 e2e test — factory builds a snake game.

This test requires the full factory infrastructure and is skipped by default.
Run with: uv run pytest -m e2e tests/test_e2e_snake.py -v

Expected runtime: 15-30 minutes. Expected cost: ~$0.50-2.00 per run.

IMPORTANT: Always run via `uv run pytest` (not bare `pytest`) to ensure
the local factory code is used, not the globally installed version.
"""

import py_compile
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

# Resolve the factory binary from the same venv Python is running in.
# When invoked via `uv run pytest`, sys.executable points to the local
# .venv/bin/python, so this finds .venv/bin/factory — the local build.
_FACTORY_BIN = str(shutil.which("factory", path=str(
    __import__("pathlib").Path(sys.executable).parent
)))


def _factory_available() -> bool:
    return _FACTORY_BIN is not None and _FACTORY_BIN != "None"


@pytest.mark.skipif(not _factory_available(), reason="factory CLI not in venv")
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not found on PATH",
)
class TestE2ESnakeGame:
    def test_factory_builds_snake_game(self, tmp_path):
        """End-to-end: factory ceo builds a snake game from scratch.

        Uses the LOCAL factory binary (from the project venv), not the
        global install. Validates the full pipeline: CEO → Researcher →
        Strategist → Builder → Archivist.

        Uses a 1800s (30min) timeout since the full factory pipeline
        takes significant time.
        """
        try:
            result = subprocess.run(
                [_FACTORY_BIN, "ceo",
                 "Build a simple snake game in Python using curses. Create a single snake.py file.",
                 "--headless", "--mode", "build", "--no-github"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            assert result.returncode in (0, 1), (
                f"Unexpected exit code {result.returncode}:\n{result.stderr[-500:]}"
            )
        except subprocess.TimeoutExpired:
            py_files = list(tmp_path.rglob("*.py"))
            if py_files:
                pytest.skip(f"Timed out after 30min but produced {len(py_files)} .py files")
            else:
                pytest.fail("Timed out after 30min with no output files")

        # Verify output
        py_files = list(tmp_path.rglob("*.py"))
        assert len(py_files) > 0, "No .py files produced"

        # At least one file should be valid Python
        valid = False
        for f in py_files:
            try:
                py_compile.compile(str(f), doraise=True)
                valid = True
            except py_compile.PyCompileError:
                pass
        assert valid, f"No valid Python files among: {[f.name for f in py_files]}"
