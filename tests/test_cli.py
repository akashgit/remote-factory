"""Tests for factory.cli — CLI subcommand routing."""

import asyncio
import contextlib
import json
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from factory.cli import main, build_parser, _is_github_url, _slugify, _extract_project_name, _dedupe_project_path, _resolve_input, _persist_spec, _has_research_target, _build_ceo_task, _ensure_repo, _quick_classify, _welcome_wizard
from factory.models import ExperimentRecord
from factory.store import ExperimentStore


# Async mock helper for invoke_agent — returns (stdout, return_code)
def _mock_invoke_agent_ok():
    return AsyncMock(return_value=("CEO completed successfully", 0))


def _mock_invoke_agent_fail():
    return AsyncMock(return_value=("Error: agent failed", 1))


@contextlib.contextmanager
def _mock_foreground():
    """Mock the interactive foreground path: subprocess.run inside ClaudeRunner,
    worktree lifecycle, and dashboard.  Yields the subprocess.run mock."""
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    with patch("factory.runners.claude.subprocess.run", mock_run), \
         patch("factory.worktree.create_worktree",
               side_effect=lambda p, b="main": (p, "factory/run-test")), \
         patch("factory.worktree.remove_worktree"), \
         patch("factory.worktree.prune_stale", return_value=[]), \
         patch("factory.cli._read_target_branch", return_value="main"), \
         patch("factory.cli._ensure_dashboard"):
        yield mock_run


class TestParser:
    def test_detect_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["detect", "/some/path"])
        assert args.command == "detect"
        assert args.path == "/some/path"

    def test_discover_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["discover", "/some/path"])
        assert args.command == "discover"

    def test_init_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["init", "/some/path"])
        assert args.command == "init"
        assert args.reparse is False

    def test_init_with_reparse(self):
        parser = build_parser()
        args = parser.parse_args(["init", "/some/path", "--reparse"])
        assert args.reparse is True

    def test_guard_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["guard", "/path", "--baseline", "abc123"])
        assert args.command == "guard"
        assert args.baseline == "abc123"

    def test_begin_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["begin", "/path", "--hypothesis", "test hyp"])
        assert args.hypothesis == "test hyp"

    def test_finalize_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([
            "finalize", "/path", "--id", "1", "--verdict", "keep",
            "--hypothesis", "h", "--summary", "s",
        ])
        assert args.id == 1
        assert args.verdict == "keep"

    def test_finalize_with_scores(self):
        parser = build_parser()
        args = parser.parse_args([
            "finalize", "/path", "--id", "1", "--verdict", "keep",
            "--hypothesis", "h", "--summary", "s",
            "--score-before", "0.80", "--score-after", "0.85",
        ])
        assert args.score_before == 0.80
        assert args.score_after == 0.85

    def test_no_command_returns_1(self):
        assert main([]) == 1

    def test_emit_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["emit", "agent.started", "--agent", "researcher", "--project", "/p"])
        assert args.command == "emit"
        assert args.event_type == "agent.started"
        assert args.agent == "researcher"
        assert args.project == "/p"

    def test_emit_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["emit", "cycle.started"])
        assert args.agent is None
        assert args.project == "."
        assert args.data is None

    def test_ceo_mode_interactive(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "distributed eval runner", "--mode", "interactive"])
        assert args.mode == "interactive"
        assert args.path == "distributed eval runner"

    def test_ceo_path_optional(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "--mode", "interactive"])
        assert args.path is None

    def test_ceo_agent_distiller_choice(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "distiller", "--task", "test", "--project", "/p"])
        assert args.role == "distiller"

    def test_ceo_agent_failure_analyst_choice(self):
        parser = build_parser()
        args = parser.parse_args(["agent", "failure_analyst", "--task", "test", "--project", "/p"])
        assert args.role == "failure_analyst"


class TestCmdCeoInteractive:
    def test_interactive_headless_incompatible(self, capsys):
        result = main(["ceo", "an idea", "--mode", "interactive", "--headless"])
        assert result == 1
        assert "incompatible" in capsys.readouterr().err.lower()

    def test_interactive_prompt_incompatible(self, capsys):
        result = main(["ceo", "an idea", "--mode", "interactive", "--prompt", "file.md"])
        assert result == 1
        assert "mutually exclusive" in capsys.readouterr().err.lower()

    def test_interactive_focus_incompatible_new_idea(self, capsys):
        """--focus + --mode interactive is rejected for new ideas (non-directory paths)."""
        result = main(["ceo", "an idea", "--mode", "interactive", "--focus", "UI"])
        assert result == 1
        err = capsys.readouterr().err.lower()
        assert "new ideas" in err

    def test_interactive_focus_allowed_existing_project(self, tmp_path):
        """--focus + --mode interactive is allowed when path is an existing directory."""
        (tmp_path / ".git").mkdir()
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path), "--mode", "interactive", "--focus", "auth layer"])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode (Phase 0)" in task
        assert "auth layer" in task

    def test_no_path_fails(self, capsys):
        result = main(["ceo"])
        assert result == 1
        err = capsys.readouterr().err.lower()
        assert "provide" in err or "error" in err

    def test_interactive_foreground_uses_subprocess_run(self, tmp_path):
        """--mode interactive launches via subprocess.run (foreground)."""
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path), "--mode", "interactive"])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd

    def test_interactive_existing_has_ideation_block(self, tmp_path):
        """--mode interactive on an existing directory injects Ideation Mode block."""
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path), "--mode", "interactive"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode (Phase 0)" in task
        assert "existing_project: true" in task

    def test_interactive_new_idea_has_ideation_block(self):
        """--mode interactive with a non-directory path injects Ideation Mode block."""
        with _mock_foreground() as mock_run:
            main(["ceo", "build a cool CLI tool", "--mode", "interactive"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode (Phase 0)" in task
        assert "## Project Specification" not in task

    def test_interactive_task_contains_idea_text(self):
        """--mode interactive with raw idea text includes it in the Phase 0 block."""
        with _mock_foreground() as mock_run:
            main(["ceo", "distributed eval runner", "--mode", "interactive"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "distributed eval runner" in task

    def test_interactive_existing_mode_is_build(self, tmp_path):
        """--mode interactive on existing dir sets Mode: build in the CEO task."""
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path), "--mode", "interactive"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "Mode: build" in task

    def test_interactive_new_idea_mode_is_build(self):
        """--mode interactive with new idea sets Mode: build in the CEO task."""
        with _mock_foreground() as mock_run:
            main(["ceo", "weather CLI", "--mode", "interactive"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "Mode: build" in task


def _make_config(*, research_target: dict | None = None) -> dict:
    """Build a valid FactoryConfig dict for testing."""
    config: dict = {
        "goal": "test project",
        "scope": ["src/**/*.py"],
        "guards": ["Do not delete tests"],
        "eval_command": "python eval/score.py",
        "eval_threshold": 0.8,
        "constraints": ["Prefer small changes"],
    }
    if research_target is not None:
        config["research_target"] = research_target
    return config


class TestHasResearchTarget:
    def test_returns_false_no_factory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _has_research_target(tmp_path) is False

    def test_returns_false_no_research_target(self, tmp_path):
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text(json.dumps(_make_config()))
        assert _has_research_target(tmp_path) is False

    def test_returns_true_with_research_target(self, tmp_path):
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        rt = {"objective": "maximize accuracy", "metric": "accuracy",
              "target": 0.9, "run_command": "python run.py",
              "result_path": "results.json"}
        (factory_dir / "config.json").write_text(json.dumps(_make_config(research_target=rt)))
        assert _has_research_target(tmp_path) is True


class TestCmdCeoResearchIdeation:
    def test_research_headless_new_project_incompatible(self, capsys):
        result = main(["ceo", "swe-bench solver", "--mode", "research", "--headless"])
        assert result == 1
        assert "foreground" in capsys.readouterr().err.lower()

    def test_research_prompt_incompatible(self, capsys):
        result = main(["ceo", "swe-bench solver", "--mode", "research", "--prompt", "file.md"])
        assert result == 1
        assert "mutually exclusive" in capsys.readouterr().err.lower()

    def test_research_focus_works_with_existing_project(self, tmp_path):
        """--focus works with --mode research on existing projects with research_target."""
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        rt = {"objective": "maximize accuracy", "metric": "accuracy",
              "target": 0.9, "run_command": "python run.py",
              "result_path": "results.json"}
        (factory_dir / "config.json").write_text(json.dumps(_make_config(research_target=rt)))
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path), "--mode", "research", "--focus", "tokenizer"])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Focus Directive" in task
        assert "tokenizer" in task

    def test_research_focus_incompatible_new_project(self, capsys):
        """--focus with --mode research on a new idea string errors."""
        result = main(["ceo", "swe-bench solver", "--mode", "research", "--focus", "tokenizer"])
        assert result == 1
        assert "focus" in capsys.readouterr().err.lower()

    def test_research_file_input_not_ideation(self, tmp_path, capsys):
        """--mode research with a file path treats it as a spec, not an idea string."""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# My Research Project\n")
        result = main(["ceo", str(spec_file), "--mode", "research"])
        # File gets resolved as spec input, not as an idea string for ideation.
        # Errors because the resulting project has no research_target configured.
        assert result == 1
        assert "research_target" in capsys.readouterr().err

    def test_research_existing_dir_no_target_errors(self, tmp_path, capsys):
        """--mode research on existing dir without research_target errors."""
        (tmp_path / ".git").mkdir()
        result = main(["ceo", str(tmp_path), "--mode", "research"])
        assert result == 1
        assert "research_target" in capsys.readouterr().err

    def test_research_ideation_foreground_uses_subprocess_run(self):
        """--mode research with idea string launches via subprocess.run."""
        with _mock_foreground() as mock_run:
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        claude_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "claude"]
        assert len(claude_calls) == 1
        cmd = claude_calls[0][0][0]
        assert cmd[0] == "claude"

    def test_research_ideation_task_has_research_phase_0(self):
        """--mode research with idea injects Research Ideation Phase 0 block."""
        with _mock_foreground() as mock_run:
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Research Ideation Mode (Phase 0)" in task
        assert "swe-bench solver agent" in task

    def test_research_ideation_task_mode_is_build(self):
        """--mode research with idea sets Mode: build (not research) since it enters ideation first."""
        with _mock_foreground() as mock_run:
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "Mode: build" in task

    def test_research_ideation_no_interactive_block(self):
        """--mode research should NOT inject Interactive Ideation block."""
        with _mock_foreground() as mock_run:
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode" not in task

    def test_research_ideation_mentions_research_config(self):
        """--mode research ideation task mentions research config fields."""
        with _mock_foreground() as mock_run:
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "Research Target" in task
        assert "Mutable Surfaces" in task
        assert "Fixed Surfaces" in task

    def test_research_existing_project_with_target_skips_ideation(self, tmp_path):
        """--mode research on existing project WITH research_target skips ideation."""
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        rt = {"objective": "maximize accuracy", "metric": "accuracy",
              "target": 0.9, "run_command": "python run.py",
              "result_path": "results.json"}
        (factory_dir / "config.json").write_text(json.dumps(_make_config(research_target=rt)))
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path), "--mode", "research"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Research Ideation Mode" not in task
        assert "Mode: research" in task


class TestCmdDetect:
    def test_detect_no_repo(self, tmp_path, capsys):
        result = main(["detect", str(tmp_path / "nonexistent")])
        assert result == 0
        assert "no_repo" in capsys.readouterr().out

    def test_detect_no_factory(self, tmp_project, capsys):
        result = main(["detect", str(tmp_project)])
        assert result == 0
        assert "no_factory" in capsys.readouterr().out


class TestCmdDiscover:
    def test_discover_python_project(self, python_project, capsys):
        result = main(["discover", str(python_project)])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["project"]["language"] == "python"
        assert output["eval_profile"]["tier"] in ("discovered", "researched", "fallback")


class TestCmdStatus:
    def test_status_parser(self):
        parser = build_parser()
        args = parser.parse_args(["status", "/some/path"])
        assert args.command == "status"

    def test_status_no_factory(self, tmp_project, capsys):
        result = main(["status", str(tmp_project)])
        assert result == 0
        out = capsys.readouterr().out
        assert "no_factory" in out
        assert str(tmp_project) in out

    def test_status_with_factory(self, tmp_project, capsys, sample_config):
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))
        exp_id = asyncio.run(store.begin("Improve performance"))
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="Improve performance",
            change_summary="Optimized hot path",
            issue_number=None, pr_number=None,
            score_before=0.8, score_after=0.95, delta=0.15,
            verdict="keep", cost_usd=None, notes="",
        )
        asyncio.run(store.finalize(exp_id, record))

        result = main(["status", str(tmp_project)])
        assert result == 0
        out = capsys.readouterr().out
        assert "has_factory" in out
        assert "1 total" in out
        assert "1 kept" in out
        assert "0 reverted" in out
        assert "Improve performance" in out
        assert "0.950" in out
        assert sample_config.goal in out

    def test_status_with_factory_no_experiments(self, tmp_project, capsys, sample_config):
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))

        result = main(["status", str(tmp_project)])
        assert result == 0
        out = capsys.readouterr().out
        assert "has_factory" in out
        assert "Experiments: none" in out


class TestCmdHistory:
    def test_history_no_experiments(self, tmp_project, capsys, sample_config):
        import asyncio
        from factory.store import ExperimentStore
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))
        result = main(["history", str(tmp_project)])
        assert result == 0
        assert "No experiments" in capsys.readouterr().out

    def test_history_with_records(self, tmp_project, capsys, sample_config):
        """history displays formatted table when records exist."""
        import asyncio
        from datetime import datetime
        from factory.store import ExperimentStore
        from factory.models import ExperimentRecord

        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))
        exp_id = asyncio.run(store.begin("Test hypothesis"))
        record = ExperimentRecord(
            id=exp_id,
            timestamp=datetime.now(),
            hypothesis="Test hypothesis",
            change_summary="Changed stuff",
            issue_number=None,
            pr_number=None,
            score_before=0.80,
            score_after=0.85,
            delta=0.05,
            verdict="keep",
            cost_usd=1.23,
            notes="",
        )
        asyncio.run(store.finalize(exp_id, record))
        result = main(["history", str(tmp_project)])
        assert result == 0
        out = capsys.readouterr().out
        assert "keep" in out
        assert "Test hypothesis" in out


class TestCmdRun:
    def test_run_success(self, tmp_path):
        """cmd_run returns 0 when CEO agent succeeds."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()):
            result = main(["run", str(tmp_path)])
        assert result == 0

    def test_run_agent_failure(self, tmp_path):
        """cmd_run returns 1 when CEO agent fails."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_fail()):
            result = main(["run", str(tmp_path)])
        assert result == 1


class TestCmdEval:
    def test_eval_failing(self, tmp_project, capsys, sample_config):
        """cmd_eval returns 1 when eval fails."""
        import asyncio
        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))

        # The eval_command won't exist, so this will fail
        result = main(["eval", str(tmp_project)])
        assert result == 1


class TestCmdNotify:
    def test_notify_no_config(self, tmp_project, capsys, caplog, sample_config):
        """cmd_notify warns when telegram is not configured."""
        import asyncio
        import logging
        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))

        with caplog.at_level(logging.WARNING):
            result = main(["notify", str(tmp_project)])
        assert result == 0
        assert "Digest sent" in capsys.readouterr().out


class TestCmdInit:
    def test_init_missing_factory_md(self, tmp_project, capsys):
        """cmd_init returns 1 when factory.md is missing."""
        result = main(["init", str(tmp_project)])
        assert result == 1
        assert "factory.md not found" in capsys.readouterr().err

    def test_init_with_factory_md(self, tmp_project, capsys):
        """cmd_init succeeds when factory.md exists."""
        (tmp_project / "factory.md").write_text(
            "# Factory\n\n## Goal\nBuild stuff\n\n## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n## Constraints\n- small changes\n"
        )
        result = main(["init", str(tmp_project)])
        assert result == 0
        assert "Initialized" in capsys.readouterr().out


class TestCmdArchive:
    def test_archive_parser(self):
        parser = build_parser()
        args = parser.parse_args(["archive", "/some/path"])
        assert args.command == "archive"
        assert args.path == "/some/path"

    def test_archive_no_experiments(self, tmp_project, capsys, sample_config):
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))
        result = main(["archive", str(tmp_project)])
        assert result == 0
        assert "Nothing to archive" in capsys.readouterr().out

    def test_archive_with_experiments(self, tmp_project, capsys, sample_config):
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))
        exp_id = asyncio.run(store.begin("Improve throughput"))
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="Improve throughput",
            change_summary="Optimized pipeline",
            issue_number=None, pr_number=None,
            score_before=0.7, score_after=0.85, delta=0.15,
            verdict="keep", cost_usd=0.5, notes="",
        )
        asyncio.run(store.finalize(exp_id, record))

        with patch("factory.obsidian.notes.write_experiment_note") as mock_exp, \
             patch("factory.obsidian.notes.write_project_dashboard") as mock_dash, \
             patch("factory.obsidian.notes.write_strategy_note") as mock_strat, \
             patch("factory.obsidian.notes.update_memory_index"), \
             patch("factory.obsidian.notes._get_vault_path",
                   return_value=tmp_project / "vault"):
            result = main(["archive", str(tmp_project)])

        assert result == 0
        out = capsys.readouterr().out
        assert "Archived 1 experiments" in out
        mock_exp.assert_called_once()
        mock_dash.assert_called_once()
        mock_strat.assert_not_called()

    def test_archive_with_strategy(self, tmp_project, capsys, sample_config):
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))
        exp_id = asyncio.run(store.begin("Test hypothesis"))
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="Test hypothesis",
            change_summary="Changed stuff",
            issue_number=None, pr_number=None,
            score_before=0.8, score_after=0.85, delta=0.05,
            verdict="keep", cost_usd=None, notes="",
        )
        asyncio.run(store.finalize(exp_id, record))
        asyncio.run(store.write_strategy("Focus on reliability."))

        with patch("factory.obsidian.notes.write_experiment_note") as mock_exp, \
             patch("factory.obsidian.notes.write_project_dashboard") as mock_dash, \
             patch("factory.obsidian.notes.write_strategy_note") as mock_strat, \
             patch("factory.obsidian.notes.update_memory_index"), \
             patch("factory.obsidian.notes._get_vault_path",
                   return_value=tmp_project / "vault"):
            result = main(["archive", str(tmp_project)])

        assert result == 0
        mock_exp.assert_called_once()
        mock_dash.assert_called_once()
        mock_strat.assert_called_once()



class TestCmdVaultInit:
    def test_vault_init_parser(self):
        parser = build_parser()
        args = parser.parse_args(["vault-init"])
        assert args.command == "vault-init"

    def test_vault_init_calls_init_vault(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(tmp_path / "vault"))
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        result = main(["vault-init"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Factory vault initialized" in out
        assert (tmp_path / "vault" / ".obsidian").is_dir()


class TestGitHubUrlDetection:
    def test_https_url_detected(self):
        assert _is_github_url("https://github.com/user/repo") is True

    def test_https_url_with_git_suffix(self):
        assert _is_github_url("https://github.com/user/repo.git") is True

    def test_ssh_url_detected(self):
        assert _is_github_url("git@github.com:user/repo.git") is True

    def test_local_path_not_detected(self):
        assert _is_github_url("/some/local/path") is False

    def test_relative_path_not_detected(self):
        assert _is_github_url("./relative/path") is False

    def test_other_url_not_detected(self):
        assert _is_github_url("https://gitlab.com/user/repo") is False


class TestRunModeFlag:
    def test_mode_default_is_auto(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path"])
        assert args.mode == "auto"

    def test_mode_discover(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--mode", "discover"])
        assert args.mode == "discover"

    def test_mode_improve_explicit(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--mode", "improve"])
        assert args.mode == "improve"

    def test_mode_meta(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--mode", "meta"])
        assert args.mode == "meta"


class TestRunWithGitHubUrl:
    def test_run_clones_https_url(self, capsys):
        """cmd_run clones a GitHub HTTPS URL into a temp dir and invokes CEO."""
        url = "https://github.com/user/repo"
        with patch("factory.cli.subprocess.run") as mock_clone, \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-abc"), \
             patch("factory.cli._read_target_branch", return_value="main"):
            result = main(["run", url])

        assert result == 0
        mock_clone.assert_called_once_with(
            ["git", "clone", url, "/tmp/factory-abc"], check=True,
        )
        out = capsys.readouterr().out
        assert "Cloned https://github.com/user/repo" in out

    def test_run_clones_ssh_url(self, capsys):
        """cmd_run clones a GitHub SSH URL into a temp dir."""
        url = "git@github.com:user/repo.git"
        with patch("factory.cli.subprocess.run") as mock_clone, \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-xyz"), \
             patch("factory.cli._read_target_branch", return_value="main"):
            result = main(["run", url])

        assert result == 0
        mock_clone.assert_called_once_with(
            ["git", "clone", url, "/tmp/factory-xyz"], check=True,
        )
        out = capsys.readouterr().out
        assert f"Cloned {url}" in out

    def test_run_local_path_no_clone(self, tmp_path):
        """cmd_run with a local path does not clone — just invokes CEO."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path)])

        assert result == 0
        mock_agent.assert_called_once()

    def test_run_discover_mode(self, tmp_path):
        """cmd_run with --mode=discover passes discover task to CEO."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path), "--mode", "discover"])

        assert result == 0
        call_args = mock_agent.call_args
        task = call_args[0][1]  # second positional arg is the task
        assert "Discover mode" in task

    def test_run_meta_mode(self, tmp_path):
        """cmd_run with --mode=meta passes meta task to CEO."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path), "--mode", "meta"])

        assert result == 0
        call_args = mock_agent.call_args
        task = call_args[0][1]
        assert "Meta mode" in task


class TestMainErrorHandling:
    def test_main_catches_exception(self, capsys):
        """main catches exceptions from handlers and returns 1."""
        with patch("factory.cli.cmd_detect", side_effect=RuntimeError("boom")):
            result = main(["detect", "/some/path"])
        assert result == 1
        assert "boom" in capsys.readouterr().err


class TestHeartbeatParserFlags:
    def test_loop_flag_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path"])
        assert args.loop is False

    def test_loop_flag_enabled(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--loop"])
        assert args.loop is True

    def test_interval_default_1800(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path"])
        assert args.interval == 1800

    def test_interval_custom(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--interval", "60"])
        assert args.interval == 60

    def test_max_cycles_default_none(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path"])
        assert args.max_cycles is None

    def test_max_cycles_custom(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--max-cycles", "5"])
        assert args.max_cycles == 5


class TestHeartbeatLoop:
    def test_no_loop_single_run(self, tmp_path):
        """Without --loop, cmd_run executes exactly one cycle."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path)])
        assert result == 0
        mock_agent.assert_called_once()

    def test_loop_exits_after_max_cycles(self, tmp_path, capsys):
        """With --loop --max-cycles=3, runs exactly 3 cycles then exits."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main([
                "run", str(tmp_path), "--loop", "--max-cycles", "3", "--interval", "0",
            ])
        assert result == 0
        assert mock_agent.call_count == 3

        out = capsys.readouterr().out
        assert "[factory] Cycle 1 started at" in out
        assert "[factory] Cycle 2 started at" in out
        assert "[factory] Cycle 3 started at" in out
        assert "[factory] Shutting down gracefully after 3 cycles." in out

    def test_loop_single_cycle(self, tmp_path, capsys):
        """--max-cycles=1 runs one cycle, no sleep, then exits."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli._chain_modes", return_value=0):
            result = main([
                "run", str(tmp_path), "--loop", "--max-cycles", "1",
            ])
        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Cycle 1 started at" in out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_graceful_sigterm(self, tmp_path, capsys):
        """SIGTERM during sleep causes clean exit."""
        captured_handlers: dict[int, object] = {}
        original_signal = signal.signal

        def _capture_signal(signum, handler):
            captured_handlers[signum] = handler
            return original_signal(signum, handler)

        def _trigger_sigterm_after_cycle(*args, **kwargs):
            handler = captured_handlers.get(signal.SIGTERM)
            if handler and callable(handler):
                threading.Timer(0.05, handler, args=(signal.SIGTERM, None)).start()
            return ("ok", 0)

        with patch("signal.signal", side_effect=_capture_signal), \
             patch("factory.agents.runner.invoke_agent", AsyncMock(side_effect=_trigger_sigterm_after_cycle)), \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path), "--loop", "--interval", "30"])

        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_graceful_sigint(self, tmp_path, capsys):
        """SIGINT during sleep causes clean exit."""
        captured_handlers: dict[int, object] = {}
        original_signal = signal.signal

        def _capture_signal(signum, handler):
            captured_handlers[signum] = handler
            return original_signal(signum, handler)

        def _trigger_sigint_after_cycle(*args, **kwargs):
            handler = captured_handlers.get(signal.SIGINT)
            if handler and callable(handler):
                threading.Timer(0.05, handler, args=(signal.SIGINT, None)).start()
            return ("ok", 0)

        with patch("signal.signal", side_effect=_capture_signal), \
             patch("factory.agents.runner.invoke_agent", AsyncMock(side_effect=_trigger_sigint_after_cycle)), \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path), "--loop", "--interval", "30"])

        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_logs_sleep_message(self, tmp_path, capsys):
        """Verify the sleep log message appears between cycles."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli._chain_modes", return_value=0):
            result = main([
                "run", str(tmp_path), "--loop", "--max-cycles", "2", "--interval", "0",
            ])
        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Cycle 1 completed. Sleeping for 0s..." in out


# ── Factory v2: agent and ceo commands ────────────────────────


class TestCmdAgentParser:
    def test_agent_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([
            "agent", "researcher", "--task", "Research the project", "--project", "/some/path",
        ])
        assert args.command == "agent"
        assert args.role == "researcher"
        assert args.task == "Research the project"
        assert args.project == "/some/path"

    def test_agent_default_timeout(self):
        parser = build_parser()
        args = parser.parse_args([
            "agent", "builder", "--task", "Build it", "--project", "/path",
        ])
        assert args.timeout == 600.0

    def test_agent_custom_timeout(self):
        parser = build_parser()
        args = parser.parse_args([
            "agent", "evaluator", "--task", "Eval", "--project", "/path", "--timeout", "300",
        ])
        assert args.timeout == 300.0

    def test_agent_all_roles_valid(self):
        parser = build_parser()
        for role in ["researcher", "strategist", "builder", "reviewer", "evaluator", "archivist", "ceo"]:
            args = parser.parse_args(["agent", role, "--task", "test", "--project", "/path"])
            assert args.role == role


class TestCmdAgent:
    def test_agent_invokes_invoke_agent(self, tmp_path, capsys):
        """cmd_agent delegates to invoke_agent with correct args."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            result = main([
                "agent", "researcher", "--task", "Research", "--project", str(tmp_path),
            ])
        assert result == 0
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        assert call_args[0][0] == "researcher"
        assert call_args[0][1] == "Research"
        out = capsys.readouterr().out
        assert "CEO completed successfully" in out

    def test_agent_returns_nonzero_on_failure(self, tmp_path):
        """cmd_agent returns agent exit code on failure."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_fail()):
            result = main([
                "agent", "builder", "--task", "Build", "--project", str(tmp_path),
            ])
        assert result == 1


class TestCmdCeoParser:
    def test_ceo_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path"])
        assert args.command == "ceo"
        assert args.path == "/some/path"

    def test_ceo_default_mode(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path"])
        assert args.mode == "auto"

    def test_ceo_meta_mode(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path", "--mode", "meta"])
        assert args.mode == "meta"


class TestCmdCeo:
    def test_ceo_headless_invokes_ceo_agent(self, tmp_path, capsys):
        """cmd_ceo --headless spawns CEO agent via invoke_agent."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["ceo", str(tmp_path), "--headless"])
        assert result == 0
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        assert call_args[0][0] == "ceo"
        assert str(tmp_path) in call_args[0][1]

    def test_ceo_headless_meta_mode_task(self, tmp_path):
        """cmd_ceo --headless with --mode=meta includes meta instructions."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["ceo", str(tmp_path), "--mode", "meta", "--headless"])
        assert result == 0
        task = mock_agent.call_args[0][1]
        assert "Meta mode" in task

    def test_ceo_headless_clones_github_url(self, capsys):
        """cmd_ceo --headless clones a GitHub URL then invokes CEO."""
        url = "https://github.com/user/repo"
        with patch("factory.cli.subprocess.run") as mock_clone, \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli._chain_modes", return_value=0), \
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-ceo"), \
             patch("factory.cli._read_target_branch", return_value="main"):
            result = main(["ceo", url, "--headless"])
        assert result == 0
        mock_clone.assert_called_once_with(
            ["git", "clone", url, "/tmp/factory-ceo"], check=True,
        )

    def test_ceo_headless_timeout_is_2_hours(self, tmp_path):
        """CEO agent gets 7200s timeout in headless mode."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            main(["ceo", str(tmp_path), "--headless"])
        call_kwargs = mock_agent.call_args[1]
        assert call_kwargs["timeout"] == 7200.0

    def test_ceo_foreground_uses_subprocess_run(self, tmp_path):
        """cmd_ceo (default) launches claude interactively via subprocess.run."""
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path)])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--append-system-prompt" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_ceo_foreground_passes_task_as_prompt(self, tmp_path):
        """Foreground mode passes the task as the initial user message."""
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path)])
        cmd = mock_run.call_args[0][0]
        assert any(str(tmp_path) in arg for arg in cmd)

    def test_ceo_foreground_cwd_is_project(self, tmp_path):
        """Foreground mode passes cwd to subprocess.run."""
        with _mock_foreground() as mock_run:
            main(["ceo", str(tmp_path)])
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == tmp_path

    def test_ceo_parser_has_headless_flag(self):
        """Parser accepts --headless flag."""
        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path", "--headless"])
        assert args.headless is True

    def test_ceo_parser_default_not_headless(self):
        """Parser defaults to foreground (not headless)."""
        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path"])
        assert args.headless is False


class TestSlugify:
    def test_basic_slug(self):
        assert _slugify("Locals Know") == "locals-know"

    def test_strips_special_chars(self):
        assert _slugify("Betty Terminal — AI-Native") == "betty-terminal-ai-native"

    def test_truncates_to_50(self):
        long_name = "a" * 100
        assert len(_slugify(long_name)) <= 50

    def test_empty_string(self):
        assert _slugify("") == "factory-project"

    def test_special_only(self):
        assert _slugify("!!!") == "factory-project"




class TestExtractProjectName:
    def test_strips_build_verb(self):
        assert _extract_project_name("Build a weather CLI tool") == "weather-cli-tool"

    def test_strips_create_verb(self):
        assert _extract_project_name("Create an API server") == "api-server"

    def test_strips_filler_adjectives(self):
        assert _extract_project_name("Build a comprehensive e-commerce platform with payments") == "e-commerce-platform-payments"

    def test_caps_at_four_words(self):
        result = _extract_project_name("distributed eval runner for multi-node benchmarks on GPUs")
        assert result == "distributed-eval-runner-multi-node"

    def test_no_verb_prefix(self):
        assert _extract_project_name("weather CLI") == "weather-cli"

    def test_strips_multiple_fillers(self):
        assert _extract_project_name("Build a simple lightweight modern REST API") == "rest-api"

    def test_empty_after_stripping_falls_back(self):
        result = _extract_project_name("build a the")
        assert result == "build-a-the"

    def test_preserves_hyphenated_words(self):
        assert _extract_project_name("real-time chat app") == "real-time-chat-app"

    def test_setup_verb(self):
        assert _extract_project_name("Set up a deployment pipeline") == "deployment-pipeline"


class TestDedupeProjectPath:
    def test_no_existing_dir(self, tmp_path):
        path = tmp_path / "projects" / "my-app"
        assert _dedupe_project_path(path, "Build a todo app") == path

    def test_existing_dir_no_spec(self, tmp_path):
        path = tmp_path / "projects" / "my-app"
        path.mkdir(parents=True)
        assert _dedupe_project_path(path, "Build a todo app") == path

    def test_existing_dir_same_spec_reuses(self, tmp_path):
        path = tmp_path / "projects" / "my-app"
        spec_dir = path / ".factory" / "strategy"
        spec_dir.mkdir(parents=True)
        (spec_dir / "current.md").write_text("## Project Specification\n\nBuild a todo app\n")
        assert _dedupe_project_path(path, "Build a todo app") == path

    def test_existing_dir_different_spec_appends_suffix(self, tmp_path):
        path = tmp_path / "projects" / "rest-api"
        spec_dir = path / ".factory" / "strategy"
        spec_dir.mkdir(parents=True)
        (spec_dir / "current.md").write_text("## Project Specification\n\nBuild a REST API for users\n")
        result = _dedupe_project_path(path, "Build a REST API for payments")
        assert result == tmp_path / "projects" / "rest-api-2"

    def test_multiple_collisions(self, tmp_path):
        base = tmp_path / "projects" / "rest-api"
        for suffix in ("", "-2", "-3"):
            d = base.parent / f"{base.name}{suffix}" if suffix else base
            spec_dir = d / ".factory" / "strategy"
            spec_dir.mkdir(parents=True)
            (spec_dir / "current.md").write_text(f"## Project Specification\n\nvariant {suffix}\n")
        result = _dedupe_project_path(base, "yet another REST API")
        assert result == tmp_path / "projects" / "rest-api-4"

    def test_resolve_input_dedupes_raw_prompt(self, tmp_path):
        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            p1, _ = _resolve_input("Build a REST API")
            p2, _ = _resolve_input("Create a new REST API")
        assert p1.name == "rest-api"
        assert p2.name == "rest-api-2"


class TestPersistSpec:
    def test_writes_spec_file(self, tmp_path):
        _persist_spec(tmp_path, "Build a todo app")
        spec = (tmp_path / ".factory" / "strategy" / "current.md").read_text()
        assert "Build a todo app" in spec

    def test_does_not_overwrite_existing(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("Existing strategy")

        _persist_spec(tmp_path, "New spec")
        assert "Existing strategy" in (strategy_dir / "current.md").read_text()

    def test_creates_directories(self, tmp_path):
        _persist_spec(tmp_path, "some spec")
        assert (tmp_path / ".factory" / "strategy" / "current.md").exists()


class TestResolveInput:
    def test_existing_dir(self, tmp_path):
        project_path, context = _resolve_input(str(tmp_path))
        assert project_path == tmp_path
        assert context is None

    def test_idea_file(self, tmp_path):
        idea_file = tmp_path / "My Project \u2014 Something Cool.md"
        idea_file.write_text("# Build something cool")

        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            project_path, context = _resolve_input(str(idea_file))

        assert project_path.name == "my-project"
        assert (project_path / ".git").is_dir()
        assert context is not None
        assert "Build something cool" in context

    def test_raw_prompt(self, tmp_path):
        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            project_path, context = _resolve_input("Build a todo app with FastAPI")

        assert project_path.parent == tmp_path / "projects"
        assert project_path.name == "todo-app-fastapi"
        assert (project_path / ".git").is_dir()
        assert context == "Build a todo app with FastAPI"

    def test_non_md_file(self, tmp_path):
        py_file = tmp_path / "script.py"
        py_file.write_text("print('hello')")

        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            project_path, context = _resolve_input(str(py_file))

        assert project_path.name == "script"
        assert (project_path / ".git").is_dir()
        assert context == "print('hello')"

    def test_binary_file_raises(self, tmp_path):
        bin_file = tmp_path / "data.bin"
        bin_file.write_bytes(b"\x00\x01\x02\xff")

        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"), \
             pytest.raises(UnicodeDecodeError):
            _resolve_input(str(bin_file))

    def test_ceo_receives_context(self, tmp_path):
        """When an idea file is given, its content reaches the CEO task."""
        idea_file = tmp_path / "Test Idea \u2014 Details.md"
        idea_file.write_text("# Test Idea\nBuild X that does Y")

        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"), \
             patch("factory.cli._chain_modes", return_value=0), \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            main(["ceo", str(idea_file), "--headless"])

        task_arg = mock_agent.call_args[0][1]  # second positional = task
        assert "Build X that does Y" in task_arg
        assert "Project Specification" in task_arg

    def test_dir_overrides_slug_for_raw_prompt(self, tmp_path):
        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            project_path, context = _resolve_input("Build a todo app with FastAPI", dir_name="my-todo")

        assert project_path.name == "my-todo"
        assert (project_path / ".git").is_dir()

    def test_dir_overrides_slug_for_idea_file(self, tmp_path):
        idea_file = tmp_path / "Long Idea Name — Details.md"
        idea_file.write_text("# Build something")

        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            project_path, context = _resolve_input(str(idea_file), dir_name="custom-name")

        assert project_path.name == "custom-name"
        assert (project_path / ".git").is_dir()

    def test_dir_ignored_for_existing_directory(self, tmp_path):
        project_path, context = _resolve_input(str(tmp_path), dir_name="ignored-name")

        assert project_path == tmp_path
        assert context is None

    def test_dir_is_slugified(self, tmp_path):
        with patch("factory.cli._get_projects_dir", return_value=tmp_path / "projects"):
            project_path, context = _resolve_input("Build something", dir_name="My Cool Project!")

        assert project_path.name == "my-cool-project"

    def test_ceo_parser_accepts_dir_argument(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "Build something", "--dir", "my-project"])
        assert args.dir == "my-project"


class TestResearchMode:
    def test_ceo_parser_accepts_research_mode(self):
        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path", "--mode", "research"])
        assert args.mode == "research"

    def test_run_parser_accepts_research_mode(self):
        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--mode", "research"])
        assert args.mode == "research"

    def test_tmux_parser_accepts_research_mode(self):
        parser = build_parser()
        args = parser.parse_args(["tmux", "/some/path", "--mode", "research"])
        assert args.mode == "research"

    def test_research_mode_task_text(self, tmp_path):
        """--mode research includes research-specific instructions in the CEO task."""
        (tmp_path / ".git").mkdir()
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        rt = {"objective": "maximize accuracy", "metric": "accuracy",
              "target": 0.9, "run_command": "python run.py",
              "result_path": "results.json"}
        (factory_dir / "config.json").write_text(json.dumps(_make_config(research_target=rt)))
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["ceo", str(tmp_path), "--mode", "research", "--headless"])
        assert result == 0
        task = mock_agent.call_args[0][1]
        assert "Research mode" in task
        assert "research_target" in task

    def test_auto_detect_research_mode(self, tmp_project, sample_config):
        """Auto-detection returns 'research' when config has research_target."""
        from factory.models import ResearchTarget
        from factory.store import ExperimentStore

        rt = ResearchTarget(
            objective="Minimize loss",
            metric="val_loss",
            target=0.01,
            run_command="python train.py",
            result_path="metrics.json",
        )
        config_with_research = sample_config.model_copy(update={"research_target": rt})
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(config_with_research))

        from factory.cli import _auto_detect_mode
        mode = _auto_detect_mode(tmp_project, force_fresh=True)
        assert mode == "research"

    def test_auto_detect_improve_without_research(self, tmp_project, sample_config):
        """Auto-detection returns 'improve' when no research_target is set."""
        store = ExperimentStore(tmp_project)
        asyncio.run(store.init(sample_config))

        from factory.cli import _auto_detect_mode
        mode = _auto_detect_mode(tmp_project, force_fresh=True)
        assert mode == "improve"


class TestBuildCeoTaskInteractive:
    """Unit tests for _build_ceo_task interactive_existing parameter."""

    def test_existing_project_emits_ideation_section(self, tmp_path):
        task = _build_ceo_task(tmp_path, "build", interactive_existing=True)
        assert "## Interactive Ideation Mode (Phase 0)" in task
        assert "existing_project: true" in task
        assert "existing project" in task

    def test_existing_project_with_focus(self, tmp_path):
        task = _build_ceo_task(tmp_path, "build", interactive_existing=True, focus="auth layer")
        assert "## Interactive Ideation Mode (Phase 0)" in task
        assert "auth layer" in task
        assert "Focus topic" in task

    def test_existing_project_without_focus(self, tmp_path):
        task = _build_ceo_task(tmp_path, "build", interactive_existing=True)
        assert "No specific topic was provided" in task

    def test_new_idea_emits_ideation_section(self, tmp_path):
        task = _build_ceo_task(tmp_path, "build", interactive_idea="weather CLI")
        assert "## Interactive Ideation Mode (Phase 0)" in task
        assert "weather CLI" in task

    def test_existing_uses_same_header_as_new_idea(self, tmp_path):
        """Both new ideas and existing projects use the same Phase 0 header."""
        existing_task = _build_ceo_task(tmp_path, "build", interactive_existing=True)
        new_task = _build_ceo_task(tmp_path, "build", interactive_idea="weather CLI")
        assert "## Interactive Ideation Mode (Phase 0)" in existing_task
        assert "## Interactive Ideation Mode (Phase 0)" in new_task

    def test_existing_project_has_existing_flag(self, tmp_path):
        """Existing project task includes the existing_project flag for CEO conditionals."""
        task = _build_ceo_task(tmp_path, "build", interactive_existing=True)
        assert "existing_project: true" in task

    def test_existing_mode_is_build(self, tmp_path):
        """When ceo_mode is build (as set by cli.py), task shows Mode: build."""
        task = _build_ceo_task(tmp_path, "build", interactive_existing=True)
        assert "Mode: build" in task


class TestCmdHomeReturnsFactoryDir:
    def test_cmd_home_returns_package_root(self, capsys):
        from factory.cli import cmd_home
        import argparse
        result = cmd_home(argparse.Namespace())
        assert result == 0
        output = capsys.readouterr().out.strip()
        assert "site-packages" not in output or Path(output).is_dir()
        assert (Path(output) / "templates").is_dir()
        assert (Path(output) / "cli.py").is_file()


class TestCmdTmuxBareCLI:
    def test_tmux_command_uses_bare_factory(self):
        """cmd_tmux generates a shell command using bare 'factory run', not uv run."""
        from factory.cli import cmd_tmux
        import argparse

        with patch("factory.cli._tmux_available", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 1})()  # has-session fails
            mock_run.side_effect = [
                type("R", (), {"returncode": 1})(),  # has-session → no existing session
                type("R", (), {"returncode": 0})(),   # new-session → success
            ]
            args = argparse.Namespace(
                path="/tmp/test-project",
                session=None,
                attach=False,
                mode=None,
                loop=True,
                interval=None,
                max_cycles=None,
                model=None,
                runner=None,
                no_github=False,
                profile=None,
            )
            result = cmd_tmux(args)
            assert result == 0

            new_session_call = mock_run.call_args_list[1]
            shell_cmd = new_session_call[0][0][-1]  # last arg is the shell command
            assert "factory run" in shell_cmd
            assert "uv run python -m factory" not in shell_cmd
            assert "cd " not in shell_cmd
            assert "source .venv/bin/activate" not in shell_cmd


class TestPluginAgentsDirGuard:
    def test_plugin_agents_dir_none_when_missing(self, tmp_path):
        """_PLUGIN_AGENTS_DIR is None when the agents/ dir doesn't exist."""
        from factory.agents import plugin
        original = plugin._PLUGIN_AGENTS_DIR
        try:
            plugin._PLUGIN_AGENTS_DIR = None
            result = plugin.check_agents_in_sync()
            assert result == []
        finally:
            plugin._PLUGIN_AGENTS_DIR = original


class TestResolveProjectPath:
    def test_cmd_notify_resolves_relative_path(self, tmp_path, capsys):
        """cmd_notify resolves relative paths so .name is non-empty."""
        from factory.cli import cmd_notify
        import argparse

        with patch("factory.cli._run", side_effect=lambda c: []), \
             patch("factory.notify.telegram.TelegramNotifier") as MockNotifier:
            mock_instance = MockNotifier.return_value
            mock_instance.send_digest = AsyncMock()
            args = argparse.Namespace(path=str(tmp_path))
            cmd_notify(args)
            call_args = mock_instance.send_digest.call_args[0]
            assert call_args[0] != ""

    def test_cmd_archive_resolves_relative_path(self, tmp_path, capsys):
        """cmd_archive resolves paths and uses non-empty project_path.name."""
        from factory.cli import cmd_archive
        import argparse

        project_path = tmp_path / "my-project"
        project_path.mkdir()
        (project_path / ".factory").mkdir()

        with patch("factory.cli._run", side_effect=lambda c: []):
            args = argparse.Namespace(path=str(project_path))
            result = cmd_archive(args)
            assert result == 0
            output = capsys.readouterr().out.strip()
            assert "Nothing to archive" in output


class TestNoBareUvRunPythonMFactory:
    """Guard: no file should use 'uv run python -m factory' — use bare 'factory' instead."""

    SCAN_GLOBS = [
        "factory/agents/prompts/*.md",
        "factory/cli.py",
        "SKILL.md",
        "README.md",
        "docs/**/*.md",
    ]

    def test_no_hardcoded_uv_run_python_m_factory(self):
        import glob
        repo_root = Path(__file__).resolve().parent.parent
        violations: list[str] = []
        for pattern in self.SCAN_GLOBS:
            for filepath in glob.glob(str(repo_root / pattern), recursive=True):
                with open(filepath) as f:
                    for lineno, line in enumerate(f, 1):
                        if "uv run python -m factory" in line:
                            violations.append(f"{Path(filepath).relative_to(repo_root)}:{lineno}")
        assert violations == [], (
            "Found 'uv run python -m factory' — use bare 'factory' instead:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


class TestSacredRule8Present:
    """Guard: Sacred Rule 8 (CEO must not do another agent's job) must exist in the CEO prompt."""

    REQUIRED_PHRASES = [
        "Do not do another agent's job",
        "Sacred Rule 8",
        "never take over the agent's work yourself",
    ]

    def test_sacred_rule_8_in_ceo_prompt(self):
        repo_root = Path(__file__).resolve().parent.parent
        ceo_prompt = (repo_root / "factory" / "agents" / "prompts" / "ceo.md").read_text()
        missing = [p for p in self.REQUIRED_PHRASES if p not in ceo_prompt]
        assert missing == [], (
            "Sacred Rule 8 is missing or incomplete in ceo.md. Missing phrases:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_sacred_rule_8_in_sacred_rules_section(self):
        """Rule 8 must be in the numbered Sacred Rules list, not just mentioned elsewhere."""
        repo_root = Path(__file__).resolve().parent.parent
        ceo_prompt = (repo_root / "factory" / "agents" / "prompts" / "ceo.md").read_text()
        assert '8. **Do not do another agent\'s job**' in ceo_prompt, (
            "Sacred Rule 8 must be a numbered item (8.) in the Sacred Rules section"
        )


class TestEnsureRepo:
    """Tests for _ensure_repo() — verifies repos are initialized with at least one commit."""

    def test_new_repo_has_commit(self, tmp_path):
        """_ensure_repo() should create a repo with at least one commit."""
        project = tmp_path / "new-project"
        _ensure_repo(project)
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=project, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert int(result.stdout.strip()) >= 1

    def test_new_repo_has_valid_branch(self, tmp_path):
        """_ensure_repo() should produce a repo with a resolvable default branch ref."""
        project = tmp_path / "new-project"
        _ensure_repo(project)
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project, capture_output=True, text=True,
        )
        assert result.returncode == 0
        branch = result.stdout.strip()
        assert branch and branch != "HEAD"

    def test_idempotent_on_existing_repo(self, tmp_path):
        """Calling _ensure_repo() on an already-initialized repo should be a no-op."""
        project = tmp_path / "existing"
        _ensure_repo(project)
        count_before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=project, capture_output=True, text=True,
        ).stdout.strip()
        _ensure_repo(project)
        count_after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=project, capture_output=True, text=True,
        ).stdout.strip()
        assert count_before == count_after


class TestInteractiveFileInput:
    """Tests for interactive mode with file path input (Bug 1 fix)."""

    def test_file_content_becomes_interactive_idea(self, tmp_path):
        """When --mode interactive receives a file path, the file content should be used as the idea."""
        spec_file = tmp_path / "my-cool-app.md"
        spec_file.write_text("Build a weather dashboard with real-time updates")
        with _mock_foreground() as mock_run:
            main(["ceo", str(spec_file), "--mode", "interactive"])
        cmd = mock_run.call_args[0][0]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "Build a weather dashboard with real-time updates" in task

    def test_slug_derived_from_filename(self, tmp_path, capsys):
        """The project slug should come from the file stem, not the full path."""
        spec_file = tmp_path / "weather-dashboard.md"
        spec_file.write_text("Build a weather dashboard")
        with _mock_foreground():
            main(["ceo", str(spec_file), "--mode", "interactive"])
        output = capsys.readouterr().out
        assert "weather-dashboard" in output
        assert "Idea file: weather-dashboard.md" in output

    def test_raw_idea_persists_spec(self, tmp_path):
        """When --mode interactive receives a raw string, the spec should be persisted."""
        with _mock_foreground(), \
             patch("factory.cli._get_projects_dir", return_value=tmp_path):
            main(["ceo", "Build a CLI todo app", "--mode", "interactive"])
        matches = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert len(matches) == 1
        spec_path = matches[0] / ".factory" / "strategy" / "current.md"
        assert spec_path.exists()
        assert "Build a CLI todo app" in spec_path.read_text()


class TestWizardLongInputRedirect:
    """Tests for wizard long-input redirect to ~/.factory/wizard_input.md."""

    def _make_input_fn(self, first_response):
        """Return an input() replacement that returns first_response then raises EOFError."""
        call_count = 0

        def _input(prompt=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_response
            raise EOFError

        return _input

    def test_long_input_triggers_file_write(self, tmp_path, monkeypatch):
        """Input >200 chars is written to ~/.factory/wizard_input.md with matching content."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"

        long_input = "a" * 250
        monkeypatch.setattr("builtins.input", self._make_input_fn(long_input))

        _welcome_wizard()

        assert wizard_file.exists()
        assert wizard_file.read_text() == long_input

    def test_short_input_no_file_written(self, tmp_path, monkeypatch):
        """Input <=200 chars does NOT write a file."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"

        short_input = "Build a weather CLI"
        monkeypatch.setattr("builtins.input", self._make_input_fn(short_input))

        with patch("factory.cli._classify_with_llm", return_value=([], [
            {"label": "Build", "explanation": "Build it.", "command": "factory ceo 'Build a weather CLI' --mode build"},
        ])):
            _welcome_wizard()

        assert not wizard_file.exists()

    def test_long_path_not_redirected(self, tmp_path, monkeypatch):
        """A long string that is an existing directory is NOT redirected."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"

        long_dir = tmp_path / ("a" * 210)
        long_dir.mkdir()

        monkeypatch.setattr("builtins.input", self._make_input_fn(str(long_dir)))

        _welcome_wizard()

        assert not wizard_file.exists()

    def test_long_url_not_redirected(self, tmp_path, monkeypatch):
        """A long GitHub URL is NOT redirected."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"

        long_url = "https://github.com/user/" + "r" * 200
        monkeypatch.setattr("builtins.input", self._make_input_fn(long_url))

        _welcome_wizard()

        assert not wizard_file.exists()

    def test_wizard_file_inside_factory_dir(self, tmp_path, monkeypatch):
        """The written file is inside ~/.factory/."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        long_input = "x" * 250
        monkeypatch.setattr("builtins.input", self._make_input_fn(long_input))

        _welcome_wizard()

        wizard_file = fake_home / ".factory" / "wizard_input.md"
        assert wizard_file.exists()
        assert wizard_file.parent == fake_home / ".factory"


class TestQuickClassifyWizardFile:
    """Tests for _quick_classify returning two options for wizard-generated files."""

    def test_wizard_file_returns_two_options(self, tmp_path, monkeypatch):
        """_quick_classify returns build and interactive options for wizard_input.md."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"
        wizard_file.parent.mkdir(parents=True)
        wizard_file.write_text("some long idea text")

        result = _quick_classify(str(wizard_file))
        assert result is not None
        assert len(result) == 2
        assert result[0]["label"] == "Build from this idea"
        assert "--mode build" in result[0]["command"]
        assert result[1]["label"] == "Brainstorm and refine first"
        assert "--mode interactive" in result[1]["command"]

    def test_regular_file_returns_one_option(self, tmp_path):
        """_quick_classify returns one option for a regular spec file."""
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# My project spec")

        result = _quick_classify(str(spec_file))
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "Build from this spec file"

    def test_wizard_file_with_tilde_path(self, tmp_path, monkeypatch):
        """_quick_classify handles ~/. factory/wizard_input.md with tilde expansion."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"
        wizard_file.parent.mkdir(parents=True)
        wizard_file.write_text("idea content")

        result = _quick_classify("~/.factory/wizard_input.md")
        assert result is not None
        assert len(result) == 2
