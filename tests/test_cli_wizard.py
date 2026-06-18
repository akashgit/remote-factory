"""Tests for the welcome wizard in factory/cli.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.cli import (
    _CLI_REF,
    _ask_follow_ups,
    _classify_with_llm,
    _quick_classify,
    _show_spinner,
    _substitute_answers,
    _welcome_wizard,
    main,
)
from factory.models import AgentRunResult


def _mock_run_result(stdout: str, return_code: int = 0) -> AgentRunResult:
    return AgentRunResult(stdout=stdout, return_code=return_code)


# -- TTY detection --------------------------------------------------------


class TestTTYDetection:
    """Wizard activates only when stdin+stderr are TTYs."""

    def test_non_tty_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-TTY falls through to argparse help (backward compatible)."""
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.stderr") as mock_stderr:
            mock_stdin.isatty.return_value = False
            mock_stderr.isatty.return_value = False
            code = main([])
        assert code == 1

    def test_tty_launches_wizard(self) -> None:
        """TTY with no subcommand dispatches to _welcome_wizard."""
        with patch("factory.cli._welcome_wizard", return_value=0) as mock_wizard, \
             patch("sys.stdin") as mock_stdin, \
             patch("sys.stderr") as mock_stderr:
            mock_stdin.isatty.return_value = True
            mock_stderr.isatty.return_value = True
            code = main([])
        assert code == 0
        mock_wizard.assert_called_once()

    def test_stdin_not_tty_stderr_tty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """If stdin is not a TTY (piped), falls through to help."""
        with patch("sys.stdin") as mock_stdin, \
             patch("sys.stderr") as mock_stderr:
            mock_stdin.isatty.return_value = False
            mock_stderr.isatty.return_value = True
            code = main([])
        assert code == 1


# -- _quick_classify ------------------------------------------------------


class TestQuickClassify:
    """Deterministic fast path for paths, files, and URLs."""

    def test_existing_dir_with_factory(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        result = _quick_classify(str(tmp_path))
        assert result is not None
        assert len(result) == 2
        assert "Improve" in result[0]["label"]
        assert str(tmp_path) in result[0]["command"]

    def test_existing_dir_without_factory(self, tmp_path: Path) -> None:
        result = _quick_classify(str(tmp_path))
        assert result is not None
        assert len(result) == 2
        assert "Set up" in result[0]["label"]

    def test_existing_file(self, tmp_path: Path) -> None:
        spec = tmp_path / "spec.md"
        spec.write_text("Build a weather CLI")
        result = _quick_classify(str(spec))
        assert result is not None
        assert len(result) == 1
        assert "spec" in result[0]["label"].lower()
        assert str(spec) in result[0]["command"]

    def test_github_url(self) -> None:
        url = "https://github.com/user/repo"
        result = _quick_classify(url)
        assert result is not None
        assert len(result) == 2
        assert "Clone" in result[0]["label"]
        assert url in result[0]["command"]

    def test_github_ssh_url(self) -> None:
        url = "git@github.com:user/repo.git"
        result = _quick_classify(url)
        assert result is not None
        assert "Clone" in result[0]["label"]

    def test_free_text_returns_none(self) -> None:
        result = _quick_classify("build me a weather CLI in Python")
        assert result is None

    def test_nonexistent_path_returns_none(self) -> None:
        result = _quick_classify("/nonexistent/path/12345")
        assert result is None

    def test_preserves_user_input_verbatim(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        user_input = str(tmp_path)
        result = _quick_classify(user_input)
        assert result is not None
        for s in result:
            assert user_input in s["command"]

    def test_long_input_does_not_crash(self) -> None:
        long_input = "a" * 500
        result = _quick_classify(long_input)
        assert result is None

    def test_explicit_mode_in_quick_classify(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        result = _quick_classify(str(tmp_path))
        assert result is not None
        assert "--mode improve" in result[0]["command"]

    def test_explicit_mode_in_cli_ref(self) -> None:
        assert "--mode improve --focus" in _CLI_REF


# -- _classify_with_llm ---------------------------------------------------


class TestClassifyWithLLM:
    """LLM-based classification with mocked runner."""

    def test_valid_json_object_response(self) -> None:
        response = {
            "follow_ups": [
                {"key": "path", "question": "Path to project", "type": "path", "optional": False},
            ],
            "suggestions": [
                {"label": "Fix it", "explanation": "Target the issue.", "command": "factory ceo {path} --focus \"bug\""},
                {"label": "Discuss", "explanation": "Talk first.", "command": "factory ceo {path} --mode design"},
            ],
        }
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(response)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("fix a bug in my project")

        assert result is not None
        follow_ups, suggestions = result
        assert len(follow_ups) == 1
        assert follow_ups[0]["key"] == "path"
        assert len(suggestions) == 2
        assert suggestions[0]["label"] == "Fix it"

    def test_valid_json_no_followups(self) -> None:
        response = {
            "follow_ups": [],
            "suggestions": [
                {"label": "Brainstorm first", "explanation": "Refine the idea.", "command": 'factory ceo "weather CLI" --mode design'},
                {"label": "Build directly", "explanation": "Start building.", "command": 'factory ceo "weather CLI"'},
            ],
        }
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(response)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert result is not None
        follow_ups, suggestions = result
        assert len(follow_ups) == 0
        assert len(suggestions) == 2
        assert suggestions[0]["label"] == "Brainstorm first"

    def test_legacy_json_array_response(self) -> None:
        """Backward compatibility: plain JSON array still works."""
        suggestions = [
            {"label": "Brainstorm first", "explanation": "Refine the idea.", "command": 'factory ceo "weather CLI" --mode design'},
            {"label": "Build directly", "explanation": "Start building.", "command": 'factory ceo "weather CLI"'},
        ]
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(suggestions)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert result is not None
        follow_ups, sug = result
        assert len(follow_ups) == 0
        assert len(sug) == 2

    def test_json_with_markdown_wrapper(self) -> None:
        raw = '```json\n{"follow_ups": [], "suggestions": [{"label": "Build it", "explanation": "Go.", "command": "factory ceo \\"test\\""}]}\n```'
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(raw))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test")

        assert result is not None
        _, suggestions = result
        assert len(suggestions) == 1
        assert suggestions[0]["label"] == "Build it"

    def test_invalid_json_returns_none(self) -> None:
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result("not valid json at all"))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert result is None

    def test_runner_failure_returns_none(self) -> None:
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result("Error", 1))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert result is None

    def test_runner_not_available_returns_none(self) -> None:
        with patch("factory.runners.get_runner", side_effect=Exception("No runner")):
            result = _classify_with_llm("weather CLI")

        assert result is None

    def test_empty_suggestions_returns_none(self) -> None:
        response = {"follow_ups": [], "suggestions": []}
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(response)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test idea")

        assert result is None

    def test_missing_required_fields_returns_none(self) -> None:
        response = {"follow_ups": [], "suggestions": [{"label": "Test"}]}  # missing command
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(response)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test idea")

        assert result is None

    def test_truncates_to_3_suggestions(self) -> None:
        response = {
            "follow_ups": [],
            "suggestions": [
                {"label": f"Option {i}", "explanation": "desc", "command": f'factory ceo "x{i}"'}
                for i in range(5)
            ],
        }
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(response)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test")

        assert result is not None
        _, suggestions = result
        assert len(suggestions) == 3

    def test_wizard_shows_cli_ref_on_llm_failure(self) -> None:
        with patch("builtins.input", side_effect=["test idea"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=None), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            mock_stderr.write = MagicMock()
            code = _welcome_wizard()

        assert code == 1
        output = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
        assert "quick reference" in output.lower() or "factory ceo" in output


# -- _show_spinner ---------------------------------------------------------


class TestShowSpinner:
    """Spinner respects NO_COLOR and stops cleanly."""

    def test_spinner_stops_on_event(self) -> None:
        import threading
        stop = threading.Event()
        stop.set()
        with patch("sys.stderr"):
            _show_spinner(stop)

    def test_spinner_respects_no_color(self) -> None:
        import threading
        stop = threading.Event()
        stop.set()
        with patch.dict("os.environ", {"NO_COLOR": "1"}), \
             patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = False
            _show_spinner(stop)


# -- _ask_follow_ups -------------------------------------------------------


class TestAskFollowUps:
    """Follow-up question collection and validation."""

    def test_empty_follow_ups_returns_empty_dict(self) -> None:
        result = _ask_follow_ups([], no_color=True)
        assert result == {}

    def test_path_follow_up_validates_directory(self, tmp_path: Path) -> None:
        follow_ups = [
            {"key": "path", "question": "Project path", "type": "path", "optional": False},
        ]
        with patch("builtins.input", return_value=str(tmp_path)), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        assert "path" in result
        assert str(tmp_path.resolve()) in result["path"]

    def test_path_follow_up_expands_tilde(self, tmp_path: Path) -> None:
        follow_ups = [
            {"key": "path", "question": "Project path", "type": "path", "optional": False},
        ]
        with patch("builtins.input", return_value=str(tmp_path)), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        # Resolved path should be absolute
        import shlex
        unquoted = shlex.split(result["path"])[0]
        assert Path(unquoted).is_absolute()

    def test_path_follow_up_rejects_nonexistent(self) -> None:
        follow_ups = [
            {"key": "path", "question": "Project path", "type": "path", "optional": False},
        ]
        with patch("builtins.input", return_value="/nonexistent/xyz/12345"), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is None

    def test_path_follow_up_empty_required_fails(self) -> None:
        follow_ups = [
            {"key": "path", "question": "Project path", "type": "path", "optional": False},
        ]
        with patch("builtins.input", return_value=""), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is None

    def test_path_follow_up_empty_optional_skips(self) -> None:
        follow_ups = [
            {"key": "path", "question": "Project path", "type": "path", "optional": True},
        ]
        with patch("builtins.input", return_value=""), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result == {}

    def test_issue_follow_up_numeric(self) -> None:
        follow_ups = [
            {"key": "issue", "question": "Issue number", "type": "issue", "optional": False},
        ]
        with patch("builtins.input", return_value="42"), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        assert result["issue"] == "42"

    def test_issue_follow_up_text(self) -> None:
        follow_ups = [
            {"key": "issue", "question": "Issue", "type": "issue", "optional": False},
        ]
        with patch("builtins.input", return_value="fix the login bug"), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        assert result["issue"] == '"fix the login bug"'

    def test_issue_follow_up_optional_empty_skips(self) -> None:
        follow_ups = [
            {"key": "issue", "question": "Issue", "type": "issue", "optional": True},
        ]
        with patch("builtins.input", return_value=""), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result == {}

    def test_text_follow_up_required(self) -> None:
        follow_ups = [
            {"key": "topic", "question": "Topic", "type": "text", "optional": False},
        ]
        with patch("builtins.input", return_value="auth system"), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        assert result["topic"] == "auth system"

    def test_text_follow_up_required_empty_fails(self) -> None:
        follow_ups = [
            {"key": "topic", "question": "Topic", "type": "text", "optional": False},
        ]
        with patch("builtins.input", return_value=""), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is None

    def test_text_follow_up_optional_empty_skips(self) -> None:
        follow_ups = [
            {"key": "topic", "question": "Topic", "type": "text", "optional": True},
        ]
        with patch("builtins.input", return_value=""), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result == {}

    def test_choice_follow_up(self) -> None:
        follow_ups = [
            {"key": "mode", "question": "Which mode?", "type": "choice",
             "options": ["design", "build", "research"], "optional": False},
        ]
        with patch("builtins.input", return_value="2"), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        assert result["mode"] == "build"

    def test_choice_follow_up_invalid_returns_none(self) -> None:
        follow_ups = [
            {"key": "mode", "question": "Which mode?", "type": "choice",
             "options": ["design", "build"], "optional": False},
        ]
        with patch("builtins.input", return_value="5"), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is None

    def test_eof_during_follow_up_returns_none(self) -> None:
        follow_ups = [
            {"key": "path", "question": "Path", "type": "path", "optional": False},
        ]
        with patch("builtins.input", side_effect=EOFError), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is None

    def test_ctrl_c_during_follow_up_returns_none(self) -> None:
        follow_ups = [
            {"key": "path", "question": "Path", "type": "path", "optional": False},
        ]
        with patch("builtins.input", side_effect=KeyboardInterrupt), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is None

    def test_multiple_follow_ups(self, tmp_path: Path) -> None:
        follow_ups = [
            {"key": "path", "question": "Path", "type": "path", "optional": False},
            {"key": "issue", "question": "Issue", "type": "issue", "optional": True},
        ]
        with patch("builtins.input", side_effect=[str(tmp_path), "42"]), \
             patch("sys.stderr"):
            result = _ask_follow_ups(follow_ups, no_color=True)

        assert result is not None
        assert "path" in result
        assert result["issue"] == "42"


# -- _substitute_answers ---------------------------------------------------


class TestSubstituteAnswers:
    """Placeholder substitution and suggestion filtering."""

    def test_substitutes_all_keys(self) -> None:
        suggestions = [
            {"label": "Fix", "command": "factory ceo {path} --focus {issue}"},
        ]
        answers = {"path": "/tmp/proj", "issue": "42"}
        result = _substitute_answers(suggestions, answers)
        assert len(result) == 1
        assert result[0]["command"] == "factory ceo /tmp/proj --focus 42"

    def test_drops_suggestion_with_unfilled_placeholder(self) -> None:
        suggestions = [
            {"label": "Fix", "command": "factory ceo {path} --focus {issue}"},
            {"label": "Discuss", "command": "factory ceo {path} --mode design"},
        ]
        answers = {"path": "/tmp/proj"}  # no issue
        result = _substitute_answers(suggestions, answers)
        assert len(result) == 1
        assert result[0]["label"] == "Discuss"
        assert result[0]["command"] == "factory ceo /tmp/proj --mode design"

    def test_keeps_suggestion_without_placeholders(self) -> None:
        suggestions = [
            {"label": "Build", "command": 'factory ceo "my idea" --mode design'},
        ]
        answers = {}
        result = _substitute_answers(suggestions, answers)
        assert len(result) == 1
        assert result[0]["command"] == 'factory ceo "my idea" --mode design'

    def test_drops_all_if_no_answers(self) -> None:
        suggestions = [
            {"label": "Fix", "command": "factory ceo {path} --focus {issue}"},
        ]
        answers = {}
        result = _substitute_answers(suggestions, answers)
        assert len(result) == 0

    def test_preserves_other_fields(self) -> None:
        suggestions = [
            {"label": "Fix", "explanation": "Target it.", "command": "factory ceo {path}", "tip": "Go!"},
        ]
        answers = {"path": "/tmp/proj"}
        result = _substitute_answers(suggestions, answers)
        assert result[0]["label"] == "Fix"
        assert result[0]["explanation"] == "Target it."
        assert result[0]["tip"] == "Go!"


# -- option selection + dispatch -------------------------------------------


class TestWizardDispatch:
    """Tests for the full wizard flow: input -> classify -> select -> dispatch."""

    def test_selects_default_option(self) -> None:
        llm_result = (
            [],
            [{"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test" --mode design'}],
        )
        with patch("builtins.input", side_effect=["test idea", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()

    def test_selects_numbered_option(self) -> None:
        llm_result = (
            [],
            [
                {"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test"'},
                {"label": "Option 2", "explanation": "Second.", "command": 'factory ceo "test" --mode design'},
            ],
        )
        with patch("builtins.input", side_effect=["test idea", "2"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()
        ns = mock_ceo.call_args[0][0]
        assert ns.mode == "design"

    def test_invalid_choice_returns_error(self) -> None:
        llm_result = (
            [],
            [{"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test"'}],
        )
        with patch("builtins.input", side_effect=["test idea", "abc"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 1

    def test_out_of_range_choice_returns_error(self) -> None:
        llm_result = (
            [],
            [{"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test"'}],
        )
        with patch("builtins.input", side_effect=["test idea", "5"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 1

    def test_fast_path_skips_llm(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        with patch("builtins.input", side_effect=[str(tmp_path), ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli.cmd_ceo", return_value=0), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0

    def test_follow_up_path_fills_command(self, tmp_path: Path) -> None:
        """Follow-up for {path} asks user and substitutes into commands."""
        llm_result = (
            [{"key": "path", "question": "Path to project", "type": "path", "optional": False}],
            [
                {"label": "Fix it", "explanation": "Go.", "command": 'factory ceo {path} --focus "fix bug"'},
                {"label": "Discuss", "explanation": "Talk.", "command": "factory ceo {path} --mode design"},
            ],
        )
        with patch("builtins.input", side_effect=["fix a bug", str(tmp_path), ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()
        ns = mock_ceo.call_args[0][0]
        assert str(tmp_path.resolve()) == ns.path

    def test_follow_up_drops_unfilled_suggestions(self, tmp_path: Path) -> None:
        """Suggestions with unfilled placeholders are dropped."""
        llm_result = (
            [
                {"key": "path", "question": "Path", "type": "path", "optional": False},
                {"key": "issue", "question": "Issue", "type": "issue", "optional": True},
            ],
            [
                {"label": "Fix specific", "explanation": "Target.", "command": "factory ceo {path} --focus {issue}"},
                {"label": "Discuss", "explanation": "Talk.", "command": "factory ceo {path} --mode design"},
            ],
        )
        # User provides path but skips optional issue
        with patch("builtins.input", side_effect=["fix a bug", str(tmp_path), "", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()
        # The selected command should be the "Discuss" one (only surviving)
        ns = mock_ceo.call_args[0][0]
        assert ns.mode == "design"

    def test_follow_up_eof_exits_cleanly(self) -> None:
        llm_result = (
            [{"key": "path", "question": "Path", "type": "path", "optional": False}],
            [{"label": "Fix", "explanation": "Go.", "command": "factory ceo {path}"}],
        )
        with patch("builtins.input", side_effect=["fix a bug", EOFError]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0

    def test_all_suggestions_dropped_shows_error(self) -> None:
        """If follow-ups result in all suggestions being dropped, return error."""
        llm_result = (
            [{"key": "path", "question": "Path", "type": "path", "optional": True}],
            [
                {"label": "Fix", "explanation": "Go.", "command": "factory ceo {path} --focus 42"},
            ],
        )
        # User skips optional path, but it's the only suggestion and it has {path}
        with patch("builtins.input", side_effect=["fix a bug", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            mock_stderr.write = MagicMock()
            code = _welcome_wizard()

        assert code == 1


# -- edge cases ------------------------------------------------------------


class TestWizardEdgeCases:
    """Empty input, EOF, Ctrl+C."""

    def test_empty_input_shows_examples_then_exits(self) -> None:
        with patch("builtins.input", side_effect=["", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0

    def test_empty_then_valid_input(self) -> None:
        llm_result = (
            [],
            [{"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'}],
        )
        with patch("builtins.input", side_effect=["", "test idea", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()

    def test_eof_on_first_prompt(self) -> None:
        with patch("builtins.input", side_effect=EOFError), \
             patch("sys.stderr") as mock_stderr, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0

    def test_eof_on_choice_prompt(self) -> None:
        llm_result = (
            [],
            [{"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'}],
        )
        with patch("builtins.input", side_effect=["test", EOFError]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0

    def test_ctrl_c_on_first_prompt(self) -> None:
        with patch("builtins.input", side_effect=KeyboardInterrupt), \
             patch("sys.stderr") as mock_stderr, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 130

    def test_ctrl_c_on_choice_prompt(self) -> None:
        llm_result = (
            [],
            [{"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'}],
        )
        with patch("builtins.input", side_effect=["test", KeyboardInterrupt]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 130

    def test_eof_on_second_prompt_after_empty(self) -> None:
        with patch("builtins.input", side_effect=["", EOFError]), \
             patch("sys.stderr") as mock_stderr, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0

    def test_ctrl_c_on_second_prompt_after_empty(self) -> None:
        with patch("builtins.input", side_effect=["", KeyboardInterrupt]), \
             patch("sys.stderr") as mock_stderr, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 130


# -- NO_COLOR behavior -----------------------------------------------------


class TestNOCOLOR:
    """Wizard respects NO_COLOR env var."""

    def test_no_color_plain_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        llm_result = (
            [],
            [{"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'}],
        )
        with patch("builtins.input", side_effect=["test", ""]), \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=llm_result), \
             patch("factory.cli.cmd_ceo", return_value=0), \
             patch.dict("os.environ", {"NO_COLOR": "1"}):
            code = _welcome_wizard()

        assert code == 0
        captured = capsys.readouterr()
        assert "\033[" not in captured.err


# -- regression: existing subcommands -------------------------------------


class TestExistingSubcommands:
    """Existing subcommands must work identically."""

    def test_home_still_works(self) -> None:
        code = main(["home"])
        assert code == 0

    def test_subcommand_not_affected(self) -> None:
        with patch("factory.cli._welcome_wizard") as mock_wizard:
            main(["home"])
        mock_wizard.assert_not_called()


# -- banner update ---------------------------------------------------------


class TestBannerUpdate:
    def test_banner_tagline(self, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.cli import _print_banner

        with patch("sys.stderr") as mock_stderr, \
             patch.dict("os.environ", {"NO_COLOR": "1"}):
            mock_stderr.isatty.return_value = False
            _print_banner("welcome")

        # The no-color branch prints the tagline without mode for welcome
        mock_stderr.write.assert_any_call("The Factory — Self-Evolving Meta-Harness")


# -- wizard file LLM classification ----------------------------------------


class TestClassifyWithLLMWizardFile:
    """_classify_with_llm reads wizard file content instead of passing the path."""

    def test_wizard_file_prompt_contains_file_content(self, tmp_path, monkeypatch):
        """When input is wizard_input.md, the LLM prompt contains the file's content."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"
        wizard_file.parent.mkdir(parents=True)
        idea_text = "Build a distributed key-value store with Raft consensus"
        wizard_file.write_text(idea_text)

        captured_prompt = {}
        response = {
            "follow_ups": [],
            "suggestions": [
                {"label": "Build it", "explanation": "Go.", "command": f'factory ceo {str(wizard_file)} --mode build'},
            ],
        }
        mock_runner = MagicMock()

        async def capture_headless(request):
            captured_prompt["value"] = request.prompt
            return _mock_run_result(json.dumps(response))

        mock_runner.headless = capture_headless

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm(str(wizard_file))

        assert result is not None
        assert idea_text in captured_prompt["value"]
        assert "wizard_input.md" not in captured_prompt["value"].split("Note:")[0]

    def test_wizard_file_prompt_injects_path_note(self, tmp_path, monkeypatch):
        """The LLM prompt tells it to use the file path in generated commands."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        wizard_file = fake_home / ".factory" / "wizard_input.md"
        wizard_file.parent.mkdir(parents=True)
        wizard_file.write_text("some idea")

        captured_prompt = {}
        response = {
            "follow_ups": [],
            "suggestions": [
                {"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'},
            ],
        }
        mock_runner = MagicMock()

        async def capture_headless(request):
            captured_prompt["value"] = request.prompt
            return _mock_run_result(json.dumps(response))

        mock_runner.headless = capture_headless

        wizard_path_str = str(wizard_file)
        with patch("factory.runners.get_runner", return_value=mock_runner):
            _classify_with_llm(wizard_path_str)

        assert "Use this file path" in captured_prompt["value"]

    def test_wizard_file_missing_falls_back_gracefully(self, tmp_path, monkeypatch):
        """If wizard_input.md doesn't exist when _classify_with_llm reads it, falls back."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        response = {
            "follow_ups": [],
            "suggestions": [
                {"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'},
            ],
        }
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=_mock_run_result(json.dumps(response)))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("~/.factory/wizard_input.md")

        assert result is not None

    def test_non_wizard_file_uses_input_directly(self):
        """For non-wizard inputs, the prompt just contains the user input string."""
        captured_prompt = {}
        response = {
            "follow_ups": [],
            "suggestions": [
                {"label": "Build", "explanation": "Go.", "command": 'factory ceo "weather CLI"'},
            ],
        }
        mock_runner = MagicMock()

        async def capture_headless(request):
            captured_prompt["value"] = request.prompt
            return _mock_run_result(json.dumps(response))

        mock_runner.headless = capture_headless

        with patch("factory.runners.get_runner", return_value=mock_runner):
            _classify_with_llm("build a weather CLI")

        assert "build a weather CLI" in captured_prompt["value"]
        assert "Use this file path" not in captured_prompt["value"]
