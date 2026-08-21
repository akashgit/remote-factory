"""Tests for CLI mode deprecation warnings."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from factory.cli._helpers import (
    CEO_MODES,
    DEAD_MODES,
    DEPRECATED_MODES,
    RUN_MODES,
    warn_deprecated_mode,
)


EXPECTED_DEPRECATED = frozenset(
    {
        "research",
        "meta",
        "review",
        "refine",
    }
)

EXPECTED_DEAD = {
    "build": "design",
    "improve": "design",
    "discover": "design",
    "interactive": "design",
    "parallel-improve": "design",
}


def test_deprecated_modes_exact_set():
    assert DEPRECATED_MODES == EXPECTED_DEPRECATED


def test_dead_modes_exact_map():
    assert DEAD_MODES == EXPECTED_DEAD


def test_deprecated_modes_subset_of_known_modes():
    all_known = set(CEO_MODES) | set(RUN_MODES) | {"refine", "review"}
    for mode in DEPRECATED_MODES:
        assert mode in all_known, f"{mode} is deprecated but not a known CLI mode"


def test_dead_modes_not_in_ceo_or_run():
    for mode in DEAD_MODES:
        assert mode not in CEO_MODES, f"dead mode {mode} still in CEO_MODES"
        assert mode not in RUN_MODES, f"dead mode {mode} still in RUN_MODES"
        assert mode not in DEPRECATED_MODES, f"dead mode {mode} still in DEPRECATED_MODES"


class TestWarnDeprecatedMode:
    def test_deprecated_mode_emits_structlog(self):
        import structlog

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
                    warn_deprecated_mode("research")
                finally:
                    _helpers.log = orig_log
            mock_warn.assert_called_once_with(
                "deprecated_cli_mode", mode="research", replacement="design"
            )
        finally:
            structlog.configure(processors=old_processors)

    def test_deprecated_mode_prints_stderr(self, capsys):
        with patch("factory.cli._helpers.log"):
            warn_deprecated_mode("research")
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "--mode research is deprecated" in captured.err
        assert "--mode design instead" in captured.err
        assert "remains functional" in captured.err

    def test_dead_mode_does_not_warn(self, capsys):
        with patch("factory.cli._helpers.log") as mock_log:
            warn_deprecated_mode("build")
        mock_log.warning.assert_not_called()
        captured = capsys.readouterr()
        assert captured.err == ""

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


class TestAutoDetectMigratesDeadModes:
    """_auto_detect_mode migrates dead modes from stale cycle state."""

    @pytest.mark.parametrize("dead_mode", sorted(EXPECTED_DEAD))
    def test_cycle_state_dead_mode_migrated(self, tmp_path, dead_mode):
        from datetime import datetime, timezone

        from factory.cli._mode_handlers import _auto_detect_mode
        from factory.models import CycleState

        state_dir = tmp_path / ".factory" / "state"
        state_dir.mkdir(parents=True)
        state = CycleState(
            cycle_id="test-cycle",
            started_at=datetime.now(tz=timezone.utc),
            mode=dead_mode,
            respawns=0,
        )
        (state_dir / "cycle.json").write_text(state.model_dump_json())

        mode = _auto_detect_mode(tmp_path)
        assert mode == "design"


class TestValidateCeoFlagsModeAliases:
    """_validate_ceo_flags migrates interactive alias and strips project: prefix."""

    def _make_args(self, mode: str, path: str = "/tmp/proj"):
        ns = argparse.Namespace()
        ns.mode = mode
        ns.path = path
        ns.headless = False
        ns.prompt = None
        ns.from_plan = None
        ns.just_plan = False
        ns.focus = None
        ns.refine = None
        ns.auto_approve = False
        ns.clean_pr = False
        ns.plugin = False
        ns.plugin_folder = None
        return ns

    def test_interactive_alias_becomes_design(self):
        from factory.cli._ceo_helpers import _validate_ceo_flags

        result = _validate_ceo_flags(self._make_args("interactive"))
        assert result[0] == "design"

    def test_project_prefix_stripped(self):
        from factory.cli._ceo_helpers import _validate_ceo_flags

        result = _validate_ceo_flags(self._make_args("project:design"))
        assert result[0] == "design"
