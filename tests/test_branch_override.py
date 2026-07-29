"""Tests for branch override propagation in CEO task building.

Verifies that the resolved base_branch (from --branch flag, factory.md config,
or git detection) flows through to _build_ceo_task, so the CEO always receives
the ## Branch Override section when the target branch is non-default.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from factory.cli.ceo import _build_ceo_task
from factory.cli._helpers import _read_target_branch


class TestBranchOverrideBug:
    """Regression tests for issue #1046: branch=None silently drops Branch Override."""

    def test_branch_none_omits_override(self):
        """When branch=None is passed, ## Branch Override must NOT appear.

        This is the pre-fix behavior replicated: the raw CLI `branch` parameter
        (None when --branch is not used) was forwarded directly, so config-derived
        branches were silently dropped.
        """
        task = _build_ceo_task(Path("/test"), "improve", branch=None)
        assert "## Branch Override" not in task

    def test_branch_explicit_includes_override(self):
        """When branch is an explicit string, ## Branch Override MUST appear."""
        task = _build_ceo_task(Path("/test"), "improve", branch="develop")
        assert "## Branch Override" in task
        assert "develop" in task

    def test_branch_override_contains_correct_value(self):
        """The Branch Override section must reference the exact branch name."""
        task = _build_ceo_task(Path("/test"), "improve", branch="release/v2")
        assert "Target branch for all PRs and merges: `release/v2`" in task


class TestBranchResolutionEndToEnd:
    """End-to-end tests covering all 3 branch sources flowing through to the task."""

    def test_explicit_branch_flag(self):
        """--branch flag: explicit value flows through to Branch Override."""
        task = _build_ceo_task(Path("/test"), "improve", branch="release")
        assert "## Branch Override" in task
        assert "`release`" in task

    def test_factory_config_branch(self, tmp_path: Path):
        """factory.md config: target_branch from .factory/config.json is resolved."""
        config_dir = tmp_path / ".factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(json.dumps({"target_branch": "develop"}))

        resolved = _read_target_branch(tmp_path)
        assert resolved == "develop"

        task = _build_ceo_task(tmp_path, "improve", branch=resolved)
        assert "## Branch Override" in task
        assert "`develop`" in task

    def test_git_default_branch_detection(self, tmp_path: Path):
        """Git detection: when no --branch and no factory.md config, detect from git."""
        subprocess.run(["git", "init", "-b", "trunk"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=tmp_path,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@test.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@test.com",
                "HOME": str(tmp_path),
                "PATH": subprocess.check_output(
                    ["bash", "-c", "echo $PATH"], text=True
                ).strip(),
            },
        )

        resolved = _read_target_branch(tmp_path)
        assert resolved == "trunk"

        task = _build_ceo_task(tmp_path, "improve", branch=resolved)
        assert "## Branch Override" in task
        assert "`trunk`" in task

    def test_config_takes_precedence_over_git(self, tmp_path: Path):
        """Config target_branch wins over git-detected default branch."""
        subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=tmp_path,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@test.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@test.com",
                "HOME": str(tmp_path),
                "PATH": subprocess.check_output(
                    ["bash", "-c", "echo $PATH"], text=True
                ).strip(),
            },
        )
        config_dir = tmp_path / ".factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(json.dumps({"target_branch": "staging"}))

        resolved = _read_target_branch(tmp_path)
        assert resolved == "staging"
