"""E2E test: factory agent builder builds a Snake game.

This test validates the FULL pipeline: factory CLI -> invoke_agent -> runner -> subprocess.
It should only run after per-capability tests pass.

Run: uv run pytest tests/test_e2e_snake.py -v -m e2e
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def build_project(tmp_path):
    """Create a minimal project directory with git init for the builder."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    (tmp_path / ".factory").mkdir(exist_ok=True)
    return tmp_path


class TestSnakeGameBuild:
    """Factory agent builder builds a canvas-based Snake game."""

    async def test_factory_builds_snake_game(self, build_project):
        project_dir = build_project

        task = (
            "Create a single file called `index.html` that implements a Snake game. "
            "Requirements:\n"
            "- Single self-contained index.html file with inline CSS and JavaScript\n"
            "- Use an HTML5 <canvas> element for rendering\n"
            "- Arrow key controls for snake movement (addEventListener for keydown)\n"
            "- Display a score counter that increments when food is eaten\n"
            "- Snake grows when it eats food\n"
            "- Game ends when snake hits wall or itself\n"
            "Only create `index.html`. Do not create any other files."
        )

        result = subprocess.run(
            [
                "factory", "agent", "builder",
                "--runner", "claude",
                "--task", task,
                "--project", str(project_dir),
                "--timeout", "300",
            ],
            capture_output=True,
            text=True,
            timeout=320,
        )

        # 1. Exit code 0
        assert result.returncode == 0, (
            f"factory agent builder failed (exit {result.returncode}):\n"
            f"STDOUT: {result.stdout[:500]}\nSTDERR: {result.stderr[:500]}"
        )

        # 2. index.html exists
        index_html = project_dir / "index.html"
        assert index_html.exists(), (
            f"index.html not created. Files in project: {list(project_dir.iterdir())}"
        )

        content = index_html.read_text()

        # 3. Contains expected snake game elements
        assert "<canvas" in content.lower(), "Missing <canvas> element"
        assert "addEventListener" in content, "Missing addEventListener for controls"
        assert "score" in content.lower(), "Missing score display"

        # 4. Valid HTML (basic structure check)
        content_lower = content.lower()
        assert "<html" in content_lower or "<!doctype" in content_lower, "Not valid HTML structure"
        assert "</html>" in content_lower, "Missing closing </html> tag"
