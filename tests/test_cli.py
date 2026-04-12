"""Tests for factory.cli — CLI subcommand routing."""

import asyncio
import json
import subprocess
from datetime import datetime
from unittest.mock import patch

from factory.cli import main, build_parser
from factory.models import ExperimentRecord
from factory.store import ExperimentStore


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
    def test_run_missing_claude_binary(self, tmp_path, capsys):
        """cmd_run returns 1 when claude CLI is not found."""
        with patch("factory.cli.subprocess.run", side_effect=FileNotFoundError):
            result = main(["run", str(tmp_path)])
        assert result == 1
        assert "not found" in capsys.readouterr().err

    def test_run_claude_nonzero_exit(self, tmp_path, capsys):
        """cmd_run returns 1 when claude CLI exits non-zero."""
        with patch(
            "factory.cli.subprocess.run",
            side_effect=subprocess.CalledProcessError(2, "claude"),
        ):
            result = main(["run", str(tmp_path)])
        assert result == 1
        assert "exited with code 2" in capsys.readouterr().err

    def test_run_success(self, tmp_path):
        """cmd_run returns 0 when claude CLI succeeds."""
        with patch("factory.cli.subprocess.run") as mock_run:
            result = main(["run", str(tmp_path)])
        assert result == 0
        mock_run.assert_called_once()


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
             patch("factory.obsidian.notes._get_vault_path",
                   return_value=tmp_project / "vault"):
            result = main(["archive", str(tmp_project)])

        assert result == 0
        mock_exp.assert_called_once()
        mock_dash.assert_called_once()
        mock_strat.assert_called_once()


class TestMainErrorHandling:
    def test_main_catches_exception(self, capsys):
        """main catches exceptions from handlers and returns 1."""
        with patch("factory.cli.cmd_detect", side_effect=RuntimeError("boom")):
            result = main(["detect", "/some/path"])
        assert result == 1
        assert "boom" in capsys.readouterr().err
