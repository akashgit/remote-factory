"""Tests for factory.cli — CLI subcommand routing."""

import asyncio
import json
import os
import signal
from datetime import datetime
from unittest.mock import patch, AsyncMock

from factory.cli import main, build_parser, _is_github_url, _match_vault_idea, _slugify, _resolve_input, _persist_spec
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

    def test_no_command_returns_1(self):
        assert main([]) == 1


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
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
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
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            result = main(["run", str(tmp_path)])

        assert result == 0
        mock_agent.assert_called_once()

    def test_run_discover_mode(self, tmp_path):
        """cmd_run with --mode=discover passes discover task to CEO."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            result = main(["run", str(tmp_path), "--mode", "discover"])

        assert result == 0
        call_args = mock_agent.call_args
        task = call_args[0][1]  # second positional arg is the task
        assert "Discover mode" in task

    def test_run_meta_mode(self, tmp_path):
        """cmd_run with --mode=meta passes meta task to CEO."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
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
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            result = main(["run", str(tmp_path)])
        assert result == 0
        mock_agent.assert_called_once()

    def test_loop_exits_after_max_cycles(self, tmp_path, capsys):
        """With --loop --max-cycles=3, runs exactly 3 cycles then exits."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent, \
             patch("factory.cli.time.sleep") as mock_sleep:
            result = main([
                "run", str(tmp_path), "--loop", "--max-cycles", "3", "--interval", "10",
            ])
        assert result == 0
        assert mock_agent.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(10)

        out = capsys.readouterr().out
        assert "[factory] Cycle 1 started at" in out
        assert "[factory] Cycle 2 started at" in out
        assert "[factory] Cycle 3 started at" in out
        assert "[factory] Shutting down gracefully after 3 cycles." in out

    def test_loop_single_cycle(self, tmp_path, capsys):
        """--max-cycles=1 runs one cycle, no sleep, then exits."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()):
            result = main([
                "run", str(tmp_path), "--loop", "--max-cycles", "1",
            ])
        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Cycle 1 started at" in out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_graceful_sigterm(self, tmp_path, capsys):
        """SIGTERM during sleep causes clean exit."""
        def _interrupt_during_sleep(interval: int) -> None:
            os.kill(os.getpid(), signal.SIGTERM)

        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli.time.sleep", side_effect=_interrupt_during_sleep):
            result = main(["run", str(tmp_path), "--loop", "--interval", "5"])

        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_graceful_sigint(self, tmp_path, capsys):
        """SIGINT during sleep causes clean exit."""
        def _interrupt_during_sleep(interval: int) -> None:
            os.kill(os.getpid(), signal.SIGINT)

        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli.time.sleep", side_effect=_interrupt_during_sleep):
            result = main(["run", str(tmp_path), "--loop", "--interval", "5"])

        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Shutting down gracefully after 1 cycles." in out

    def test_loop_logs_sleep_message(self, tmp_path, capsys):
        """Verify the sleep log message appears between cycles."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli.time.sleep"):
            result = main([
                "run", str(tmp_path), "--loop", "--max-cycles", "2", "--interval", "60",
            ])
        assert result == 0
        out = capsys.readouterr().out
        assert "[factory] Cycle 1 completed. Sleeping for 60s..." in out


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
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            result = main(["ceo", str(tmp_path), "--headless"])
        assert result == 0
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        assert call_args[0][0] == "ceo"
        assert str(tmp_path) in call_args[0][1]

    def test_ceo_headless_meta_mode_task(self, tmp_path):
        """cmd_ceo --headless with --mode=meta includes meta instructions."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            result = main(["ceo", str(tmp_path), "--mode", "meta", "--headless"])
        assert result == 0
        task = mock_agent.call_args[0][1]
        assert "Meta mode" in task

    def test_ceo_headless_clones_github_url(self, capsys):
        """cmd_ceo --headless clones a GitHub URL then invokes CEO."""
        url = "https://github.com/user/repo"
        with patch("factory.cli.subprocess.run") as mock_clone, \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()), \
             patch("factory.cli.tempfile.mkdtemp", return_value="/tmp/factory-ceo"):
            result = main(["ceo", url, "--headless"])
        assert result == 0
        mock_clone.assert_called_once_with(
            ["git", "clone", url, "/tmp/factory-ceo"], check=True,
        )

    def test_ceo_headless_timeout_is_1_hour(self, tmp_path):
        """CEO agent gets 3600s timeout in headless mode."""
        with patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            main(["ceo", str(tmp_path), "--headless"])
        call_kwargs = mock_agent.call_args[1]
        assert call_kwargs["timeout"] == 3600.0

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


class TestMatchVaultIdea:
    def test_exact_match(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Locals Know — Restaurant Discovery.md").write_text("# Locals Know")
        (ideas_dir / "Ideas.md").write_text("# MOC")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]):
            match = _match_vault_idea("Locals Know")
        assert match is not None
        assert "Locals Know" in match.stem

    def test_short_name_match(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Betty Terminal \u2014 AI-Native Terminal.md").write_text("# Betty")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]):
            match = _match_vault_idea("Betty Terminal")
        assert match is not None

    def test_substring_match(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Kalshi Bot \u2014 High-Probability Trader.md").write_text("# Kalshi")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]):
            match = _match_vault_idea("kalshi")
        assert match is not None
        assert "Kalshi" in match.stem

    def test_no_match(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Some Idea.md").write_text("# Idea")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]):
            match = _match_vault_idea("nonexistent thing")
        assert match is None

    def test_skips_moc(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Ideas.md").write_text("# Ideas MOC")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]):
            match = _match_vault_idea("Ideas")
        assert match is None

    def test_multi_word_match(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Voice to Vault \u2014 Speak and Save.md").write_text("# V2V")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]):
            match = _match_vault_idea("voice vault")
        assert match is not None


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

    def test_vault_idea(self, tmp_path):
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "My Project \u2014 Something Cool.md").write_text("# Build something cool")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]), \
             patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"), \
             patch("factory.cli.subprocess.run"):
            project_path, context = _resolve_input("My Project")

        assert project_path.name == "my-project"
        assert context is not None
        assert "Build something cool" in context

    def test_raw_prompt(self, tmp_path):
        with patch("factory.cli._get_ideas_dirs", return_value=[tmp_path / "nope"]), \
             patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"), \
             patch("factory.cli.subprocess.run"):
            project_path, context = _resolve_input("Build a todo app with FastAPI")

        assert project_path.parent == tmp_path / "projects"
        assert context == "Build a todo app with FastAPI"

    def test_ceo_receives_context(self, tmp_path):
        """When a vault idea is matched, its content reaches the CEO task."""
        ideas_dir = tmp_path / "Ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Test Idea \u2014 Details.md").write_text("# Test Idea\nBuild X that does Y")

        with patch("factory.cli._get_ideas_dirs", return_value=[ideas_dir]), \
             patch("factory.cli._PROJECTS_DIR", tmp_path / "projects"), \
             patch("factory.cli.subprocess.run"), \
             patch("factory.agents.runner.invoke_agent", _mock_invoke_agent_ok()) as mock_agent:
            main(["ceo", "Test Idea", "--headless"])

        task_arg = mock_agent.call_args[0][1]  # second positional = task
        assert "Build X that does Y" in task_arg
        assert "Project Specification" in task_arg
