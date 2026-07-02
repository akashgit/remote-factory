"""Tests for CLI tmux commands — session naming, env propagation, flag parity, UX guards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.cli import (
    CEO_MODES,
    _tmux_session_name,
    build_parser,
    cmd_tmux,
    cmd_tmux_capture,
    cmd_tmux_ls,
    cmd_tmux_stop,
)
from factory.cli._tmux_commands import _build_tmux_run_args, _tmux_session_alive


class TestTmuxSessionName:
    def test_different_names_for_same_basename(self) -> None:
        p1 = Path("/tmp/myapp")
        p2 = Path("/home/user/myapp")
        assert _tmux_session_name(p1) != _tmux_session_name(p2)

    def test_name_includes_hash_suffix(self) -> None:
        p = Path("/tmp/myapp")
        name = _tmux_session_name(p)
        assert name.startswith("factory-myapp-")
        suffix = name.split("-")[-1]
        assert len(suffix) == 6
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_deterministic(self) -> None:
        p = Path("/tmp/myapp")
        assert _tmux_session_name(p) == _tmux_session_name(p)

    def test_same_basename_same_path_equal(self) -> None:
        p = Path("/tmp/myapp")
        assert _tmux_session_name(p) == _tmux_session_name(p)


class TestEnvVarWhitelist:
    def test_builds_correct_export_commands(self) -> None:
        env = {
            "FACTORY_MODEL": "opus",
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "BOBSHELL_API_KEY": "bob-key",
            "OPENAI_API_KEY": "sk-xxx",
            "CODEX_API_KEY": "codex-key",
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLOUD_ML_REGION": "us-central1",
            "HOME": "/home/user",
            "UNRELATED_VAR": "should-not-appear",
            "PATH": "/usr/bin:/usr/local/bin",
        }

        args = argparse.Namespace(
            path="/tmp/myproject",
            session=None,
            mode="auto",
            loop=False,
            interval=1800,
            max_cycles=None,
            attach=False,
            no_github=False,
            model=None,
            runner=None,
            profile=None,
            focus=None,
            refine=None,
            clean_pr=None,
            prompt=None,
            branch=None,
            min_growth=None,
            max_new=None,
            discover_only=False,
            bg_agents=False,
            tmux_persist=False,
            use_profile=False,
        )

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._resolve_model", return_value=None),
            patch("factory.cli._tmux_commands._save_tmux_session_mapping"),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("factory.cli._tmux_commands.time.sleep"),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", env, clear=True),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session (not found)
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0, stdout="", stderr=""),  # capture-pane
            ]
            cmd_tmux(args)

            shell_cmd = mock_run.call_args_list[1][0][0][-1]

            assert "FACTORY_MODEL=" in shell_cmd
            assert "ANTHROPIC_API_KEY=" in shell_cmd
            assert "CLAUDE_CODE_USE_VERTEX=" in shell_cmd
            assert "CLOUD_ML_REGION=" in shell_cmd
            assert "BOBSHELL_API_KEY=" in shell_cmd
            assert "OPENAI_API_KEY=" in shell_cmd
            assert "CODEX_API_KEY=" in shell_cmd
            assert "UNRELATED_VAR" not in shell_cmd
            assert "HOME=" not in shell_cmd
            assert "export PATH=" in shell_cmd
            assert "google-cloud-sdk" not in shell_cmd


class TestBuildTmuxRunArgs:
    def test_propagates_all_flags(self) -> None:
        args = argparse.Namespace(
            path="/tmp/project",
            mode="improve",
            loop=True,
            interval=900,
            max_cycles=5,
            no_github=True,
            profile="vertex",
            focus="dashboard UI",
            refine="fix login",
            clean_pr=True,
            runner="bob",
            prompt="/path/to/spec.md",
            branch="develop",
            min_growth=3,
            max_new=4,
            discover_only=True,
            bg_agents=True,
            tmux_persist=True,
            use_profile=True,
        )
        result = _build_tmux_run_args(args, Path("/tmp/project"), "opus-4")

        assert "--mode improve" in result
        assert "--loop" not in result
        assert "--interval" not in result
        assert "--max-cycles" not in result
        assert "--model" in result
        assert "--no-github" in result
        assert "--profile" in result
        assert "--focus" in result
        assert "--refine" in result
        assert "--clean-pr" in result
        assert "--runner" in result
        assert "--prompt" in result
        assert "--branch" in result
        assert "--min-growth 3" in result
        assert "--max-new 4" in result
        assert "--discover-only" in result
        assert "--bg-agents" in result
        assert "--tmux-persist" in result
        assert "--use-profile" in result

    def test_no_clean_pr(self) -> None:
        args = argparse.Namespace(
            mode=None, loop=False, interval=0, max_cycles=None,
            no_github=False, profile=None, focus=None, refine=None,
            clean_pr=False, runner=None, prompt=None, branch=None,
            min_growth=None, max_new=None, discover_only=False,
            bg_agents=False, tmux_persist=False, use_profile=False,
        )
        result = _build_tmux_run_args(args, Path("/tmp/p"), None)
        assert "--no-clean-pr" in result

    def test_minimal_args(self) -> None:
        args = argparse.Namespace(
            mode=None, loop=False, interval=0, max_cycles=None,
            no_github=False, profile=None, focus=None, refine=None,
            clean_pr=None, runner=None, prompt=None, branch=None,
            min_growth=None, max_new=None, discover_only=False,
            bg_agents=False, tmux_persist=False, use_profile=False,
        )
        result = _build_tmux_run_args(args, Path("/tmp/p"), None)
        assert result == "factory ceo /tmp/p"


class TestCmdTmuxStop:
    def test_requires_all_when_no_session_or_path(self) -> None:
        args = argparse.Namespace(session=None, path=None, stop_all=False)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="factory-app-abc123\n", stderr="",
            )
            rc = cmd_tmux_stop(args)

        assert rc == 1

    def test_all_flag_kills_sessions(self) -> None:
        args = argparse.Namespace(session=None, path=None, stop_all=True)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="factory-app-abc123\nother-session\n", stderr=""),
                MagicMock(returncode=0),  # kill-session
            ]
            rc = cmd_tmux_stop(args)

        assert rc == 0


class TestCmdTmuxLs:
    def test_json_output(self, tmp_path: Path) -> None:
        args = argparse.Namespace(json_output=True)
        mapping = {"factory-app-abc123": "/tmp/app"}

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value=mapping),
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="factory-app-abc123\t1719000000\t1",
            )
            rc = cmd_tmux_ls(args)

        assert rc == 0
        printed = mock_print.call_args[0][0]
        data = json.loads(printed)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["session"] == "factory-app-abc123"
        assert data[0]["project"] == "/tmp/app"
        assert "started" in data[0]

    def test_empty_json_output(self) -> None:
        args = argparse.Namespace(json_output=True)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={}),
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="other-session\t1719000000\t1",
            )
            cmd_tmux_ls(args)

        mock_print.assert_called_with("[]")


class TestTmuxSessionMapping:
    def test_mapping_written_on_launch(self, tmp_path: Path) -> None:
        sessions_file = tmp_path / "tmux_sessions.json"
        args = argparse.Namespace(
            path="/tmp/myproject",
            session=None,
            mode="auto",
            loop=False,
            interval=1800,
            max_cycles=None,
            attach=False,
            no_github=False,
            model=None,
            runner=None,
            profile=None,
            focus=None,
            refine=None,
            clean_pr=None,
            prompt=None,
            branch=None,
            min_growth=None,
            max_new=None,
            discover_only=False,
            bg_agents=False,
            tmux_persist=False,
            use_profile=False,
        )

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._resolve_model", return_value=None),
            patch("factory.cli._tmux_commands._TMUX_SESSIONS_FILE", sessions_file),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("factory.cli._tmux_commands.time.sleep"),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0, stdout="", stderr=""),  # capture-pane
            ]
            cmd_tmux(args)

        mapping = json.loads(sessions_file.read_text())
        assert len(mapping) == 1
        session_name = list(mapping.keys())[0]
        assert mapping[session_name] == str(Path("/tmp/myproject").resolve())

    def test_mapping_read_on_ls(self, tmp_path: Path) -> None:
        sessions_file = tmp_path / "tmux_sessions.json"
        sessions_file.write_text(json.dumps({"factory-app-abc123": "/home/user/app"}))

        args = argparse.Namespace(json_output=True)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._TMUX_SESSIONS_FILE", sessions_file),
            patch("subprocess.run") as mock_run,
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="factory-app-abc123\t1719000000\t1",
            )
            cmd_tmux_ls(args)

        printed = mock_print.call_args[0][0]
        data = json.loads(printed)
        assert data[0]["project"] == "/home/user/app"


class TestTmuxModeChoices:
    @pytest.mark.parametrize("mode", ["design", "interactive", "review", "create"])
    def test_tmux_accepts_ceo_only_modes(self, mode: str) -> None:
        parser = build_parser()
        args = parser.parse_args(["tmux", "/tmp/project", "--mode", mode])
        assert args.mode == mode

    def test_tmux_accepts_all_ceo_modes(self) -> None:
        parser = build_parser()
        for mode in CEO_MODES:
            args = parser.parse_args(["tmux", "/tmp/project", "--mode", mode])
            assert args.mode == mode


class TestTmuxSessionAlive:
    def test_returns_true_when_session_exists(self) -> None:
        with patch("factory.cli._tmux_commands.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert _tmux_session_alive("factory-app-abc123") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "factory-app-abc123"],
            capture_output=True,
        )

    def test_returns_false_when_session_missing(self) -> None:
        with patch("factory.cli._tmux_commands.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert _tmux_session_alive("factory-app-abc123") is False


class TestCmdTmuxCapture:
    def test_captures_with_session_name(self) -> None:
        args = argparse.Namespace(session="factory-app-abc123", path=None, lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="line1\nline2\n",
            )
            rc = cmd_tmux_capture(args)

        assert rc == 0
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-t", "factory-app-abc123", "-p", "-S", "-100"],
            capture_output=True,
            text=True,
        )
        mock_print.assert_called_once_with("line1\nline2\n", end="")

    def test_session_not_found(self) -> None:
        args = argparse.Namespace(session="factory-gone-abc123", path=None, lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=False),
            patch("builtins.print") as mock_print,
        ):
            rc = cmd_tmux_capture(args)

        assert rc == 1
        mock_print.assert_called_once()
        assert "not found" in mock_print.call_args[0][0]

    def test_tmux_not_available(self) -> None:
        args = argparse.Namespace(session="factory-app-abc123", path=None, lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=False),
            patch("builtins.print") as mock_print,
        ):
            rc = cmd_tmux_capture(args)

        assert rc == 1
        assert "not installed" in mock_print.call_args[0][0]

    def test_no_session_or_path(self) -> None:
        args = argparse.Namespace(session=None, path=None, lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("builtins.print") as mock_print,
        ):
            rc = cmd_tmux_capture(args)

        assert rc == 1
        assert "specify" in mock_print.call_args[0][0]

    def test_path_based_lookup_from_mapping(self) -> None:
        args = argparse.Namespace(session=None, path="/tmp/myproject", lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={"factory-myproject-abc123": "/tmp/myproject"}),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("builtins.print"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="output\n")
            rc = cmd_tmux_capture(args)

        assert rc == 0
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-t", "factory-myproject-abc123", "-p", "-S", "-100"],
            capture_output=True,
            text=True,
        )

    def test_path_based_fallback_to_session_name(self) -> None:
        args = argparse.Namespace(session=None, path="/tmp/unmapped", lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={}),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("builtins.print"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="output\n")
            rc = cmd_tmux_capture(args)

        assert rc == 0

    def test_capture_pane_failure(self) -> None:
        args = argparse.Namespace(session="factory-app-abc123", path=None, lines=-100)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            rc = cmd_tmux_capture(args)

        assert rc == 1
        assert "failed to capture" in mock_print.call_args[0][0]


class TestCmdTmuxPostDispatchVerification:
    def test_warns_when_error_markers_in_pane_output(self) -> None:
        args = argparse.Namespace(
            path="/tmp/myproject",
            session=None,
            mode="auto",
            loop=False,
            interval=1800,
            max_cycles=None,
            attach=False,
            no_github=False,
            model=None,
            runner=None,
            profile=None,
            focus=None,
            refine=None,
            clean_pr=None,
            prompt=None,
            branch=None,
            min_growth=None,
            max_new=None,
            discover_only=False,
            bg_agents=False,
            tmux_persist=False,
            use_profile=False,
        )

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._resolve_model", return_value=None),
            patch("factory.cli._tmux_commands._save_tmux_session_mapping"),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=True),
            patch("factory.cli._tmux_commands.time.sleep"),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True),
            patch("builtins.print") as mock_print,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session (not found)
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0, stdout="Error: something went wrong\n", stderr=""),  # capture-pane
            ]
            rc = cmd_tmux(args)

        assert rc == 0
        stderr_calls = [c for c in mock_print.call_args_list if c[1].get("file") is sys.stderr]
        assert any("may have errors" in str(c) for c in stderr_calls)

    def test_returns_error_when_session_dies_immediately(self) -> None:
        args = argparse.Namespace(
            path="/tmp/myproject",
            session=None,
            mode="auto",
            loop=False,
            interval=1800,
            max_cycles=None,
            attach=False,
            no_github=False,
            model=None,
            runner=None,
            profile=None,
            focus=None,
            refine=None,
            clean_pr=None,
            prompt=None,
            branch=None,
            min_growth=None,
            max_new=None,
            discover_only=False,
            bg_agents=False,
            tmux_persist=False,
            use_profile=False,
        )

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._resolve_model", return_value=None),
            patch("factory.cli._tmux_commands._save_tmux_session_mapping"),
            patch("factory.cli._tmux_commands._tmux_session_alive", return_value=False),
            patch("factory.cli._tmux_commands.time.sleep"),
            patch("subprocess.run") as mock_run,
            patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True),
            patch("builtins.print") as mock_print,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session (not found)
                MagicMock(returncode=0),  # new-session
            ]
            rc = cmd_tmux(args)

        assert rc == 1
        stderr_calls = [c for c in mock_print.call_args_list if c[1].get("file") is sys.stderr]
        assert any("exited immediately" in str(c) for c in stderr_calls)


class TestCmdTmuxStopEdgeCases:
    def test_tmux_not_available(self) -> None:
        args = argparse.Namespace(session="factory-app-abc123", path=None, stop_all=False, force=False)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=False),
            patch("builtins.print") as mock_print,
        ):
            rc = cmd_tmux_stop(args)

        assert rc == 1
        assert "not installed" in mock_print.call_args[0][0]

    def test_path_derives_session_name(self) -> None:
        args = argparse.Namespace(session=None, path="/tmp/myproject", stop_all=False, force=False)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={}),
            patch("subprocess.run") as mock_run,
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(returncode=1)  # has-session → not found
            rc = cmd_tmux_stop(args)

        assert rc == 1
        assert any("not found" in str(c) for c in mock_print.call_args_list)

    def test_session_not_found_in_tmux(self) -> None:
        args = argparse.Namespace(session="factory-gone-abc123", path=None, stop_all=False, force=False)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(returncode=1)  # has-session → not found
            rc = cmd_tmux_stop(args)

        assert rc == 1
        assert any("not found" in str(c) for c in mock_print.call_args_list)


class TestCmdTmuxStopOwnership:
    def test_warns_and_blocks_unregistered_session(self) -> None:
        args = argparse.Namespace(session="factory-mystery-abc123", path=None, stop_all=False, force=False)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={}),
            patch("subprocess.run") as mock_run,
            patch("builtins.print") as mock_print,
        ):
            mock_run.return_value = MagicMock(returncode=0)  # has-session
            rc = cmd_tmux_stop(args)

        assert rc == 1
        stderr_calls = [c for c in mock_print.call_args_list if c[1].get("file") is sys.stderr]
        assert any("not in the factory session registry" in str(c) for c in stderr_calls)
        kill_calls = [c for c in mock_run.call_args_list if "kill-session" in str(c)]
        assert len(kill_calls) == 0

    def test_force_kills_unregistered_session(self) -> None:
        args = argparse.Namespace(session="factory-mystery-abc123", path=None, stop_all=False, force=True)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={}),
            patch("subprocess.run") as mock_run,
            patch("builtins.print"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = cmd_tmux_stop(args)

        assert rc == 0

    def test_registered_session_killed_without_force(self) -> None:
        args = argparse.Namespace(session="factory-app-abc123", path=None, stop_all=False, force=False)

        with (
            patch("factory.cli._tmux_commands._tmux_available", return_value=True),
            patch("factory.cli._tmux_commands._load_tmux_session_mapping", return_value={"factory-app-abc123": "/tmp/app"}),
            patch("subprocess.run") as mock_run,
            patch("builtins.print"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = cmd_tmux_stop(args)

        assert rc == 0
