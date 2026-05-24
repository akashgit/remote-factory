"""Tests for the welcome wizard in factory/cli.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.cli import (
    _classify_with_llm,
    _quick_classify,
    _show_spinner,
    _welcome_wizard,
    main,
)


# ── TTY detection ──────────────────────────────────────────────


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


# ── _quick_classify ────────────────────────────────────────────


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


# ── _classify_with_llm ────────────────────────────────────────


class TestClassifyWithLLM:
    """LLM-based classification with mocked runner."""

    def test_valid_json_response(self) -> None:
        suggestions = [
            {"label": "Brainstorm first", "explanation": "Refine the idea.", "command": 'factory ceo "weather CLI" --mode interactive'},
            {"label": "Build directly", "explanation": "Start building.", "command": 'factory ceo "weather CLI"'},
        ]
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=(json.dumps(suggestions), 0))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert len(result) == 2
        assert result[0]["label"] == "Brainstorm first"

    def test_json_with_markdown_wrapper(self) -> None:
        raw = '```json\n[{"label": "Build it", "explanation": "Go.", "command": "factory ceo \\"test\\""}]\n```'
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=(raw, 0))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test")

        assert len(result) == 1
        assert result[0]["label"] == "Build it"

    def test_invalid_json_returns_none(self) -> None:
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=("not valid json at all", 0))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert result is None

    def test_runner_failure_returns_none(self) -> None:
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=("Error", 1))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("weather CLI")

        assert result is None

    def test_runner_not_available_returns_none(self) -> None:
        with patch("factory.runners.get_runner", side_effect=Exception("No runner")):
            result = _classify_with_llm("weather CLI")

        assert result is None

    def test_empty_array_returns_none(self) -> None:
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=("[]", 0))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test idea")

        assert result is None

    def test_missing_required_fields_returns_none(self) -> None:
        bad_suggestions = [{"label": "Test"}]  # missing command
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=(json.dumps(bad_suggestions), 0))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test idea")

        assert result is None

    def test_truncates_to_3_suggestions(self) -> None:
        suggestions = [
            {"label": f"Option {i}", "explanation": "desc", "command": f'factory ceo "x{i}"'}
            for i in range(5)
        ]
        mock_runner = MagicMock()
        mock_runner.headless = AsyncMock(return_value=(json.dumps(suggestions), 0))

        with patch("factory.runners.get_runner", return_value=mock_runner):
            result = _classify_with_llm("test")

        assert len(result) == 3

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


# ── _show_spinner ──────────────────────────────────────────────


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


# ── option selection + dispatch ────────────────────────────────


class TestWizardDispatch:
    """Tests for the full wizard flow: input -> classify -> select -> dispatch."""

    def test_selects_default_option(self) -> None:
        suggestions = [
            {"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test" --mode interactive'},
        ]
        with patch("builtins.input", side_effect=["test idea", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()

    def test_selects_numbered_option(self) -> None:
        suggestions = [
            {"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test"'},
            {"label": "Option 2", "explanation": "Second.", "command": 'factory ceo "test" --mode interactive'},
        ]
        with patch("builtins.input", side_effect=["test idea", "2"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()
        ns = mock_ceo.call_args[0][0]
        assert ns.mode == "interactive"

    def test_invalid_choice_returns_error(self) -> None:
        suggestions = [
            {"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test"'},
        ]
        with patch("builtins.input", side_effect=["test idea", "abc"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 1

    def test_out_of_range_choice_returns_error(self) -> None:
        suggestions = [
            {"label": "Option 1", "explanation": "First.", "command": 'factory ceo "test"'},
        ]
        with patch("builtins.input", side_effect=["test idea", "5"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
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

    def test_path_placeholder_prompts_for_path(self, tmp_path: Path) -> None:
        suggestions = [
            {"label": "Fix it", "explanation": "Go.", "command": 'factory ceo <path> --focus "fix bug"'},
        ]
        with patch("builtins.input", side_effect=["fix a bug", "", str(tmp_path)]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("factory.cli.cmd_ceo", return_value=0) as mock_ceo, \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0
        mock_ceo.assert_called_once()
        ns = mock_ceo.call_args[0][0]
        assert str(tmp_path.resolve()) == ns.path

    def test_path_placeholder_empty_returns_error(self) -> None:
        suggestions = [
            {"label": "Fix it", "explanation": "Go.", "command": 'factory ceo <path> --focus "fix bug"'},
        ]
        with patch("builtins.input", side_effect=["fix a bug", "", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 1

    def test_path_placeholder_nonexistent_returns_error(self) -> None:
        suggestions = [
            {"label": "Fix it", "explanation": "Go.", "command": 'factory ceo <path> --focus "fix bug"'},
        ]
        with patch("builtins.input", side_effect=["fix a bug", "", "/nonexistent/path/xyz"]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 1

    def test_path_placeholder_eof_exits_cleanly(self) -> None:
        suggestions = [
            {"label": "Fix it", "explanation": "Go.", "command": 'factory ceo <path> --focus "fix bug"'},
        ]
        with patch("builtins.input", side_effect=["fix a bug", "", EOFError]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("os.environ", {}):
            mock_stderr.isatty.return_value = True
            code = _welcome_wizard()

        assert code == 0


# ── edge cases ─────────────────────────────────────────────────


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
        suggestions = [
            {"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'},
        ]
        with patch("builtins.input", side_effect=["", "test idea", ""]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
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
        suggestions = [
            {"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'},
        ]
        with patch("builtins.input", side_effect=["test", EOFError]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
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
        suggestions = [
            {"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'},
        ]
        with patch("builtins.input", side_effect=["test", KeyboardInterrupt]), \
             patch("sys.stderr") as mock_stderr, \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
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


# ── NO_COLOR behavior ──────────────────────────────────────────


class TestNOCOLOR:
    """Wizard respects NO_COLOR env var."""

    def test_no_color_plain_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        suggestions = [
            {"label": "Build", "explanation": "Go.", "command": 'factory ceo "test"'},
        ]
        with patch("builtins.input", side_effect=["test", ""]), \
             patch("factory.cli._quick_classify", return_value=None), \
             patch("factory.cli._classify_with_llm", return_value=suggestions), \
             patch("factory.cli.cmd_ceo", return_value=0), \
             patch.dict("os.environ", {"NO_COLOR": "1"}):
            code = _welcome_wizard()

        assert code == 0
        captured = capsys.readouterr()
        assert "\033[" not in captured.err


# ── regression: existing subcommands ───────────────────────────


class TestExistingSubcommands:
    """Existing subcommands must work identically."""

    def test_home_still_works(self) -> None:
        code = main(["home"])
        assert code == 0

    def test_subcommand_not_affected(self) -> None:
        with patch("factory.cli._welcome_wizard") as mock_wizard:
            main(["home"])
        mock_wizard.assert_not_called()


# ── banner update ──────────────────────────────────────────────


class TestBannerUpdate:
    def test_banner_tagline(self, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.cli import _print_banner

        with patch("sys.stderr") as mock_stderr, \
             patch.dict("os.environ", {"NO_COLOR": "1"}):
            mock_stderr.isatty.return_value = False
            _print_banner("welcome")

        # The no-color branch prints the tagline without mode for welcome
        mock_stderr.write.assert_any_call("The Factory — Self-Evolving Meta-Harness")
