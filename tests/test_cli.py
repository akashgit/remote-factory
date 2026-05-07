"""Tests for factory.cli — CLI subcommand routing."""

import asyncio
import json
import os
import signal
import threading
from datetime import datetime
from unittest.mock import patch, AsyncMock

import pytest

from factory.cli import main, build_parser, _is_github_url, _slugify, _resolve_input, _persist_spec, _has_research_target
from factory.models import ExperimentRecord
from factory.store import ExperimentStore


# Async mock helper for invoke_agent — returns (stdout, return_code)
def _mock_invoke_agent_ok():
    return AsyncMock(return_value=("CEO completed successfully", 0))


def _mock_invoke_agent_fail():
    return AsyncMock(return_value=("Error: agent failed", 1))


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

    def test_interactive_focus_incompatible(self, capsys):
        result = main(["ceo", "an idea", "--mode", "interactive", "--focus", "UI"])
        assert result == 1
        assert "mutually exclusive" in capsys.readouterr().err.lower()

    def test_no_path_fails(self, capsys):
        result = main(["ceo"])
        assert result == 1
        err = capsys.readouterr().err.lower()
        assert "provide" in err or "error" in err

    def test_interactive_foreground_uses_execvp(self, tmp_path):
        """--mode interactive launches via os.execvp (foreground)."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path), "--mode", "interactive"])
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][1]
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd

    def test_interactive_task_has_phase_0_block(self, tmp_path):
        """--mode interactive injects Phase 0 block into the CEO task."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path), "--mode", "interactive"])
        cmd = mock_exec.call_args[0][1]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode (Phase 0)" in task

    def test_interactive_no_duplicate_context(self, tmp_path):
        """--mode interactive does not inject the idea as both Phase 0 and Project Specification."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "build a cool CLI tool", "--mode", "interactive"])
        cmd = mock_exec.call_args[0][1]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode" in task
        assert "## Project Specification" not in task

    def test_interactive_task_contains_idea_text(self, tmp_path):
        """--mode interactive with raw idea text includes it in the Phase 0 block."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "distributed eval runner", "--mode", "interactive"])
        cmd = mock_exec.call_args[0][1]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "distributed eval runner" in task

    def test_interactive_task_mode_is_build(self, tmp_path):
        """--mode interactive sets Mode: build in the CEO task (not Mode: interactive)."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path), "--mode", "interactive"])
        cmd = mock_exec.call_args[0][1]
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
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path), "--mode", "research", "--focus", "tokenizer"])
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][1]
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

    def test_research_ideation_foreground_uses_execvp(self):
        """--mode research with idea string launches via os.execvp."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][1]
        assert cmd[0] == "claude"

    def test_research_ideation_task_has_research_phase_0(self):
        """--mode research with idea injects Research Ideation Phase 0 block."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_exec.call_args[0][1]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Research Ideation Mode (Phase 0)" in task
        assert "swe-bench solver agent" in task

    def test_research_ideation_task_mode_is_build(self):
        """--mode research with idea sets Mode: build (not research) since it enters ideation first."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_exec.call_args[0][1]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "Mode: build" in task

    def test_research_ideation_no_interactive_block(self):
        """--mode research should NOT inject Interactive Ideation block."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_exec.call_args[0][1]
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        task = cmd[dsp_idx + 1]
        assert "## Interactive Ideation Mode" not in task

    def test_research_ideation_mentions_research_config(self):
        """--mode research ideation task mentions research config fields."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", "swe-bench solver agent", "--mode", "research"])
        cmd = mock_exec.call_args[0][1]
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
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path), "--mode", "research"])
        cmd = mock_exec.call_args[0][1]
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
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-abc"):
            result = main(["run", url])

        assert result == 0
        # git clone should have been called
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
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-xyz"):
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
        def _send_sigterm_after_cycle(*args, **kwargs):
            threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return ("ok", 0)

        with patch("factory.agents.runner.invoke_agent", AsyncMock(side_effect=_send_sigterm_after_cycle)), \
             patch("factory.cli._chain_modes", return_value=0):
            result = main(["run", str(tmp_path), "--loop", "--interval", "30"])

        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_graceful_sigint(self, tmp_path, capsys):
        """SIGINT during sleep causes clean exit."""
        def _send_sigint_after_cycle(*args, **kwargs):
            threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            return ("ok", 0)

        with patch("factory.agents.runner.invoke_agent", AsyncMock(side_effect=_send_sigint_after_cycle)), \
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
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-ceo"):
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

    def test_ceo_foreground_uses_execvp(self, tmp_path):
        """cmd_ceo (default) launches claude interactively via os.execvp."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path)])
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][1]
        assert cmd[0] == "claude"
        assert "--append-system-prompt" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_ceo_foreground_passes_task_as_prompt(self, tmp_path):
        """Foreground mode passes the task as the initial user message."""
        with patch("factory.cli.os.execvp") as mock_exec, \
             patch("factory.cli.os.chdir"):
            main(["ceo", str(tmp_path)])
        cmd = mock_exec.call_args[0][1]
        # Last arg (before flags) should be the task string
        # The task contains the project path
        assert any(str(tmp_path) in arg for arg in cmd)

    def test_ceo_foreground_chdir_to_project(self, tmp_path):
        """Foreground mode changes cwd to the project directory."""
        with patch("factory.cli.os.execvp"), \
             patch("factory.cli.os.chdir") as mock_chdir:
            main(["ceo", str(tmp_path)])
        mock_chdir.assert_called_once_with(tmp_path)

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

        with patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"):
            project_path, context = _resolve_input(str(idea_file))

        assert project_path.name == "my-project"
        assert (project_path / ".git").is_dir()
        assert context is not None
        assert "Build something cool" in context

    def test_raw_prompt(self, tmp_path):
        with patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"):
            project_path, context = _resolve_input("Build a todo app with FastAPI")

        assert project_path.parent == tmp_path / "projects"
        assert (project_path / ".git").is_dir()
        assert context == "Build a todo app with FastAPI"

    def test_non_md_file(self, tmp_path):
        py_file = tmp_path / "script.py"
        py_file.write_text("print('hello')")

        with patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"):
            project_path, context = _resolve_input(str(py_file))

        assert project_path.name == "script"
        assert (project_path / ".git").is_dir()
        assert context == "print('hello')"

    def test_binary_file_raises(self, tmp_path):
        bin_file = tmp_path / "data.bin"
        bin_file.write_bytes(b"\x00\x01\x02\xff")

        with patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"), \
             pytest.raises(UnicodeDecodeError):
            _resolve_input(str(bin_file))

    def test_ceo_receives_context(self, tmp_path):
        """When an idea file is given, its content reaches the CEO task."""
        idea_file = tmp_path / "Test Idea \u2014 Details.md"
        idea_file.write_text("# Test Idea\nBuild X that does Y")

        with patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"), \
             patch("factory.cli._chain_modes", return_value=0), \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            main(["ceo", str(idea_file), "--headless"])

        task_arg = mock_agent.call_args[0][1]  # second positional = task
        assert "Build X that does Y" in task_arg
        assert "Project Specification" in task_arg


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
