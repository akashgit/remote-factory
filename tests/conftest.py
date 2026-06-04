"""Shared pytest fixtures for remote-factory tests."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.models import FactoryConfig

# CRITICAL: Set FACTORY_BOB_DRY_RUN=1 before any tests run.
# This ensures BobRunner never invokes real bob during tests.
os.environ["FACTORY_BOB_DRY_RUN"] = "1"

# Disable CEO completion guard by default in tests.
# Tests that need to exercise the guard can unset this or test with mocked invoke_agent.
os.environ["FACTORY_CEO_RESPAWN_DISABLED"] = "1"


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path) -> None:
    """Redirect global registry to tmp_path during tests to avoid polluting ~/.factory/."""
    os.environ["FACTORY_REGISTRY_DIR"] = str(tmp_path / ".factory-test-registry")
    yield  # type: ignore[misc]
    os.environ.pop("FACTORY_REGISTRY_DIR", None)


@pytest.fixture(autouse=True)
def _reset_agent_failure_counter() -> None:
    """Reset consecutive agent failure counter between tests."""
    from factory.agents.runner import reset_failure_counter
    reset_failure_counter()
    yield  # type: ignore[misc]
    reset_failure_counter()


@pytest.fixture(autouse=True)
def _mock_worktree(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Stub worktree functions for tests that don't exercise worktree logic.

    Tests in test_worktree.py opt out via the 'real_worktree' marker.
    """
    if "real_worktree" in {m.name for m in request.node.iter_markers()}:
        yield  # type: ignore[misc]
        return

    def _fake_create(project_path: Path, base_branch: str = "main") -> tuple[Path, str]:
        return project_path, "factory/run-fake0000"

    def _fake_remove(project_path: Path, worktree_path: Path, branch: str) -> None:
        pass

    def _fake_prune(project_path: Path) -> list[str]:
        return []

    with patch("factory.worktree.create_worktree", side_effect=_fake_create), \
         patch("factory.worktree.remove_worktree", side_effect=_fake_remove), \
         patch("factory.worktree.prune_stale", side_effect=_fake_prune):
        yield  # type: ignore[misc]


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal project directory with git init."""
    import subprocess
    project = tmp_path / "test-project"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=project, capture_output=True, check=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com",
             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    return project


@pytest.fixture
def sample_config() -> FactoryConfig:
    """Return a sample FactoryConfig for testing."""
    return FactoryConfig(
        goal="Build a test project",
        scope=["src/**/*.py", "tests/**/*.py"],
        guards=["Do not delete tests"],
        eval_command="python eval/score.py",
        eval_threshold=0.8,
        constraints=["Prefer small changes"],
    )


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    """Create a minimal Python project with pyproject.toml and tests."""
    project = tmp_path / "my-project"
    project.mkdir()

    (project / "pyproject.toml").write_text(
        '[project]\nname = "my-project"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["pydantic>=2.0"]\n\n'
        "[tool.pytest.ini_options]\nasyncio_mode = \"auto\"\n\n"
        "[tool.ruff]\nline-length = 100\n\n"
        '[dependency-groups]\ndev = ["pytest>=8.0", "ruff>=0.8"]\n'
    )
    (project / "uv.lock").write_text("")
    (project / "my_project").mkdir()
    (project / "my_project" / "__init__.py").write_text("")
    (project / "tests").mkdir()
    (project / "tests" / "__init__.py").write_text("")
    (project / "tests" / "test_basic.py").write_text("def test_ok(): pass\n")
    (project / "README.md").write_text("# My Project\nA CLI tool.\n")

    return project


@pytest.fixture
def obsidian_vault(tmp_path: Path) -> Path:
    """Create a temporary Obsidian vault directory."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault
