"""Tests for branch override propagation — ensures resolved base_branch
reaches _build_ceo_task, not the raw CLI --branch flag."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from factory.cli._task_builder import _build_ceo_task
from factory.cli._helpers import _read_target_branch


class TestBuildCeoTaskBranch:
    """_build_ceo_task emits a Branch Override section only when branch is set."""

    def test_branch_override_appears_when_set(self, tmp_path: Path):
        task = _build_ceo_task(tmp_path, "improve", branch="develop")
        assert "## Branch Override" in task
        assert "`develop`" in task

    def test_branch_override_absent_when_none(self, tmp_path: Path):
        task = _build_ceo_task(tmp_path, "improve", branch=None)
        assert "## Branch Override" not in task

    def test_branch_override_absent_when_empty_string(self, tmp_path: Path):
        task = _build_ceo_task(tmp_path, "improve", branch="")
        assert "## Branch Override" not in task


class TestReadTargetBranch:
    """_read_target_branch reads from config.json, falling back to git."""

    def test_reads_from_config(self, tmp_path: Path):
        config_dir = tmp_path / ".factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(json.dumps({"target_branch": "release/v2"}))
        assert _read_target_branch(tmp_path) == "release/v2"

    def test_falls_back_to_git(self, tmp_path: Path):
        with patch("factory.worktree.detect_default_branch", return_value="main"):
            assert _read_target_branch(tmp_path) == "main"

    def test_ignores_malformed_config(self, tmp_path: Path):
        config_dir = tmp_path / ".factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{bad json")
        with patch("factory.worktree.detect_default_branch", return_value="main"):
            assert _read_target_branch(tmp_path) == "main"


class TestBranchPropagation:
    """Integration: verify the resolution logic used by _execute_ceo and _run_single_cycle.

    The actual callers use ``base_branch = branch or _read_target_branch(project_path)``
    and pass base_branch (not the raw branch flag) to _build_ceo_task.
    """

    def test_config_branch_resolves_when_flag_is_none(self, tmp_path: Path):
        """When --branch is None, base_branch resolves from factory config."""
        config_dir = tmp_path / ".factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({"target_branch": "staging"})
        )

        branch = None
        base_branch = branch or _read_target_branch(tmp_path)
        assert base_branch == "staging"

        task = _build_ceo_task(tmp_path, "improve", branch=base_branch)
        assert "## Branch Override" in task
        assert "`staging`" in task

    def test_explicit_flag_takes_precedence_over_config(self, tmp_path: Path):
        """When --branch is explicitly set, it wins over config."""
        config_dir = tmp_path / ".factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({"target_branch": "staging"})
        )

        branch = "feature/custom"
        base_branch = branch or _read_target_branch(tmp_path)
        assert base_branch == "feature/custom"

        task = _build_ceo_task(tmp_path, "improve", branch=base_branch)
        assert "`feature/custom`" in task

    def test_git_fallback_when_no_config(self, tmp_path: Path):
        """When no config exists, base_branch falls back to git default branch."""
        with patch("factory.worktree.detect_default_branch", return_value="main"):
            branch = None
            base_branch = branch or _read_target_branch(tmp_path)
            assert base_branch == "main"

            task = _build_ceo_task(tmp_path, "improve", branch=base_branch)
            assert "## Branch Override" in task
            assert "`main`" in task
