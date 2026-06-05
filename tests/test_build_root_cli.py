"""Tests for build-root CLI routing — Phase 2."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.cli import (
    build_parser,
    main,
    _has_build_root_config,
    _build_ceo_task,
)


def _make_config(*, build_root: dict | None = None, research_target: dict | None = None) -> dict:
    """Build a valid FactoryConfig dict for testing."""
    config: dict = {
        "goal": "test project",
        "scope": ["src/**/*.py"],
        "guards": ["Do not delete tests"],
        "eval_command": "python eval/score.py",
        "eval_threshold": 0.8,
        "constraints": ["Prefer small changes"],
    }
    if build_root is not None:
        config["build_root"] = build_root
    if research_target is not None:
        config["research_target"] = research_target
    return config


def _mock_foreground():
    """Mock the interactive foreground path."""
    import contextlib

    mock_run = MagicMock(return_value=MagicMock(returncode=0))

    @contextlib.contextmanager
    def _ctx():
        with patch("factory.runners.claude.subprocess.run", mock_run), \
             patch("factory.worktree.create_worktree",
                   side_effect=lambda p, b="main": (p, "factory/run-test")), \
             patch("factory.worktree.remove_worktree"), \
             patch("factory.worktree.prune_stale", return_value=[]), \
             patch("factory.cli._read_target_branch", return_value="main"), \
             patch("factory.cli._ensure_dashboard"):
            yield mock_run

    return _ctx()


class TestBuildRootModeChoices:
    def test_build_root_in_ceo_mode_choices(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "/path", "--mode", "build-root"])
        assert args.mode == "build-root"

    def test_build_root_in_run_mode_choices(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/path", "--mode", "build-root"])
        assert args.mode == "build-root"

    def test_build_root_in_tmux_mode_choices(self):
        parser = build_parser()
        args = parser.parse_args(["tmux", "/path", "--mode", "build-root"])
        assert args.mode == "build-root"


class TestBuildRootRequiresConfig:
    def test_build_root_requires_config(self, tmp_path, capsys):
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text(json.dumps(_make_config()))
        result = main(["ceo", str(tmp_path), "--mode", "build-root"])
        assert result == 1
        assert "build_root" in capsys.readouterr().err


class TestBuildRootAutoDetect:
    def test_returns_false_no_factory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _has_build_root_config(tmp_path) is False

    def test_returns_false_no_build_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text(json.dumps(_make_config()))
        assert _has_build_root_config(tmp_path) is False

    def test_returns_true_with_build_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        br = {"project_repo": "https://example.com/repo", "version_tag": "v1.0"}
        (factory_dir / "config.json").write_text(json.dumps(_make_config(build_root=br)))
        assert _has_build_root_config(tmp_path) is True


class TestBuildRootLoadsDedicatedPrompt:
    def test_build_root_loads_dedicated_prompt(self, tmp_path):
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        br = {"project_repo": "https://example.com/repo", "version_tag": "v1.0"}
        (factory_dir / "config.json").write_text(json.dumps(_make_config(build_root=br)))

        prompts_dir = Path(__file__).parent.parent / "factory" / "agents" / "prompts"
        prompt_file = prompts_dir / "build-root-ceo.md"
        prompt_file.write_text("# Build-Root CEO\nYou are the build-root orchestrator.")
        try:
            from factory.agents.runner import resolve_prompt
            prompt = resolve_prompt("build-root-ceo", tmp_path)
            assert "Build-Root CEO" in prompt
            assert "build-root orchestrator" in prompt
        finally:
            prompt_file.unlink(missing_ok=True)


class TestBuildRootTaskBuilder:
    def test_build_root_task_includes_pipeline(self, tmp_path):
        task = _build_ceo_task(tmp_path, mode="build-root")
        assert "Build-Root mode" in task
        assert "DEP RESOLVE" in task
        assert "COMPILE" in task
        assert "TEST" in task
        assert "known-fixes.yaml" in task
