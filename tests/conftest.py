"""Shared pytest fixtures for remote-factory tests."""

import os
from pathlib import Path

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
