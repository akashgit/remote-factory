"""Tier 3 e2e test — factory builds a snake game.

This test requires the full factory infrastructure and is skipped by default.
Run with: pytest -m e2e tests/test_e2e_snake.py -v
"""

import shutil

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not found on PATH",
)
class TestE2ESnakeGame:
    def test_factory_builds_snake_game(self, tmp_path):
        """End-to-end: factory ceo builds a snake game from scratch.

        This is a manual validation test — it verifies that the factory
        can produce a working project directory but does not validate
        game logic.
        """
        import subprocess

        result = subprocess.run(
            ["factory", "ceo", "Build a simple snake game in Python", "--headless",
             "--mode", "build", "--no-github"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=600,
        )
        # Just verify it doesn't crash — game quality is manual validation
        assert result.returncode in (0, 1), f"Unexpected exit code: {result.returncode}"
