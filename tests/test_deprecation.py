"""Tests for CLI mode deprecation warnings."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import structlog

from factory.cli._helpers import DEPRECATED_MODES, CEO_MODES, RUN_MODES, warn_deprecated_mode


EXPECTED_DEPRECATED = frozenset(
    {
        "build",
        "improve",
        "research",
        "meta",
        "discover",
        "review",
        "refine",
        "parallel-improve",
        "interactive",
    }
)


def test_deprecated_modes_exact_set():
    assert DEPRECATED_MODES == EXPECTED_DEPRECATED


def test_deprecated_modes_subset_of_known_modes():
    all_known = set(CEO_MODES) | set(RUN_MODES) | {"interactive", "refine", "review"}
    for mode in DEPRECATED_MODES:
        assert mode in all_known, f"{mode} is deprecated but not a known CLI mode"


class TestWarnDeprecatedMode:
    def test_deprecated_mode_emits_structlog(self):
        cfg = structlog.get_config()
        old_processors = cfg.get("processors", [])
        try:
            structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
            log = structlog.get_logger()
            with patch.object(log, "warning") as mock_warn:
                from factory.cli import _helpers

                orig_log = _helpers.log
                _helpers.log = log
                try:
                    warn_deprecated_mode("build")
                finally:
                    _helpers.log = orig_log
            mock_warn.assert_called_once_with(
                "deprecated_cli_mode", mode="build", replacement="design"
            )
        finally:
            structlog.configure(processors=old_processors)

    def test_deprecated_mode_prints_stderr(self, capsys):
        with patch("factory.cli._helpers.log"):
            warn_deprecated_mode("build")
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "--mode build is deprecated" in captured.err
        assert "--mode design instead" in captured.err
        assert "remains functional" in captured.err

    def test_interactive_has_alias_note(self, capsys):
        with patch("factory.cli._helpers.log"):
            warn_deprecated_mode("interactive")
        captured = capsys.readouterr()
        assert "alias for 'design'" in captured.err

    def test_create_not_deprecated(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("create")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_design_not_deprecated(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("design")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_auto_not_deprecated(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("auto")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_swebench_not_deprecated(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("swebench")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_qa_not_deprecated(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("qa")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_deep_qa_not_deprecated(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("deep-qa")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

    @pytest.mark.parametrize("mode", sorted(EXPECTED_DEPRECATED))
    def test_all_deprecated_modes_warn(self, mode, capsys):
        with patch("factory.cli._helpers.log"):
            warn_deprecated_mode(mode)
        captured = capsys.readouterr()
        assert f"--mode {mode} is deprecated" in captured.err
