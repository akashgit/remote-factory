"""Tests for factory.cli.optimize_step — workflow node helpers."""

from __future__ import annotations

import argparse
import json

import pytest

from factory.cli.optimize_step import (
    cmd_optimize_step_apply_patch,
    cmd_optimize_step_check_gate,
    _read_state,
    _write_state,
)


class TestApplyPatch:
    """Test apply-patch JSON parsing and skill mutation."""

    def test_valid_json(self, tmp_path) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        (opt_dir / "current_skill.md").write_text("# Skill\n\nBase content.\n")
        (opt_dir / "mutation.json").write_text(json.dumps({
            "rules": ["Always check the question type", "Look for named entities"],
            "reasoning": "test",
        }))

        args = argparse.Namespace(project=str(tmp_path))
        result = cmd_optimize_step_apply_patch(args)
        assert result == 0

        skill = (opt_dir / "current_skill.md").read_text()
        assert "Always check the question type" in skill
        assert "Look for named entities" in skill
        assert "## Learned Rules" in skill

    def test_markdown_wrapped_json(self, tmp_path) -> None:
        """Strategist wraps JSON in markdown code blocks — regex fallback should handle it."""
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        (opt_dir / "current_skill.md").write_text("# Skill\n")
        (opt_dir / "mutation.json").write_text(
            'Here are the rules:\n```json\n{"rules": ["Rule A"], "reasoning": "test"}\n```\n'
        )

        args = argparse.Namespace(project=str(tmp_path))
        result = cmd_optimize_step_apply_patch(args)
        assert result == 0

        skill = (opt_dir / "current_skill.md").read_text()
        assert "Rule A" in skill

    def test_empty_rules(self, tmp_path) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        (opt_dir / "current_skill.md").write_text("# Skill\n")
        (opt_dir / "mutation.json").write_text(json.dumps({"rules": [], "reasoning": "nothing"}))

        args = argparse.Namespace(project=str(tmp_path))
        result = cmd_optimize_step_apply_patch(args)
        assert result == 0

    def test_missing_mutation_file(self, tmp_path) -> None:
        args = argparse.Namespace(project=str(tmp_path))
        result = cmd_optimize_step_apply_patch(args)
        assert result == 1

    def test_unparseable_json(self, tmp_path) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)
        (opt_dir / "mutation.json").write_text("this is not json at all")

        args = argparse.Namespace(project=str(tmp_path))
        result = cmd_optimize_step_apply_patch(args)
        assert result == 1


class TestCheckGate:
    """Test check-gate verdict logic."""

    def test_baseline_positive_score(self, tmp_path) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        (opt_dir / "baseline.json").write_text(json.dumps({"score": 0.5}))

        args = argparse.Namespace(project=str(tmp_path), baseline=True)
        result = cmd_optimize_step_check_gate(args)
        assert result == 0  # PROCEED

    def test_baseline_zero_score(self, tmp_path) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        (opt_dir / "baseline.json").write_text(json.dumps({"score": 0.0}))

        args = argparse.Namespace(project=str(tmp_path), baseline=True)
        result = cmd_optimize_step_check_gate(args)
        assert result == 2  # HALT

    def test_improvement_proceed(self, tmp_path, monkeypatch) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        state = {
            "step": 2,
            "current_score": 0.7,
            "best_score": 0.7,
            "best_step": 2,
            "history": [
                {"step": 1, "score_start": 0.0, "score_end": 0.5, "score_delta": 0.5, "verdict": "pending"},
                {"step": 2, "score_start": 0.5, "score_end": 0.7, "score_delta": 0.2, "verdict": "pending"},
            ],
        }
        _write_state(tmp_path, state)
        monkeypatch.setenv("FACTORY_OPT_MAX_ITERATIONS", "5")

        args = argparse.Namespace(project=str(tmp_path), baseline=False)
        result = cmd_optimize_step_check_gate(args)
        assert result == 0  # PROCEED (improvement found)

    def test_no_improvement_reloop(self, tmp_path, monkeypatch) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        state = {
            "step": 2,
            "current_score": 0.5,
            "best_score": 0.5,
            "best_step": 1,
            "history": [
                {"step": 1, "score_start": 0.0, "score_end": 0.5, "score_delta": 0.5, "verdict": "pending"},
                {"step": 2, "score_start": 0.5, "score_end": 0.4, "score_delta": -0.1, "verdict": "pending"},
            ],
        }
        _write_state(tmp_path, state)
        monkeypatch.setenv("FACTORY_OPT_MAX_ITERATIONS", "5")

        args = argparse.Namespace(project=str(tmp_path), baseline=False)
        result = cmd_optimize_step_check_gate(args)
        assert result == 1  # RELOOP

    def test_max_iterations_proceed(self, tmp_path, monkeypatch) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        history = [
            {"step": i, "score_start": 0.5, "score_end": 0.5, "score_delta": 0.0, "verdict": "pending"}
            for i in range(1, 7)
        ]
        state = {
            "step": 6,
            "current_score": 0.5,
            "best_score": 0.5,
            "best_step": 1,
            "history": history,
        }
        _write_state(tmp_path, state)
        monkeypatch.setenv("FACTORY_OPT_MAX_ITERATIONS", "5")

        args = argparse.Namespace(project=str(tmp_path), baseline=False)
        result = cmd_optimize_step_check_gate(args)
        assert result == 0  # PROCEED (max iterations)

    def test_empty_history_halt(self, tmp_path) -> None:
        opt_dir = tmp_path / ".factory" / "optimization"
        opt_dir.mkdir(parents=True)

        state = {"step": 0, "current_score": 0.0, "best_score": 0.0, "best_step": 0, "history": []}
        _write_state(tmp_path, state)

        args = argparse.Namespace(project=str(tmp_path), baseline=False)
        result = cmd_optimize_step_check_gate(args)
        assert result == 2  # HALT


class TestStateReadWrite:
    """Test state.json read/write helpers."""

    def test_write_and_read(self, tmp_path) -> None:
        state = {"step": 1, "current_score": 0.5, "best_score": 0.5, "best_step": 1, "history": []}
        _write_state(tmp_path, state)
        loaded = _read_state(tmp_path)
        assert loaded == state

    def test_read_missing_returns_default(self, tmp_path) -> None:
        state = _read_state(tmp_path)
        assert state["step"] == 0
        assert state["history"] == []

    def test_append_only_history(self, tmp_path) -> None:
        state = _read_state(tmp_path)
        state["history"].append({"step": 1, "score_start": 0.0, "score_end": 0.5})
        _write_state(tmp_path, state)

        state = _read_state(tmp_path)
        state["history"].append({"step": 2, "score_start": 0.5, "score_end": 0.7})
        _write_state(tmp_path, state)

        final = _read_state(tmp_path)
        assert len(final["history"]) == 2


class TestOptimizeStepParser:
    """Verify argparse setup for optimize-step subcommand."""

    def test_optimize_step_parser_exists(self) -> None:
        from factory.cli._main import build_parser
        parser = build_parser()
        args = parser.parse_args(["optimize-step", "run-dev", "--project", "/tmp/p"])
        assert args.command == "optimize-step"
        assert args.optimize_step_command == "run-dev"
        assert args.project == "/tmp/p"

    def test_apply_patch_subcommand(self) -> None:
        from factory.cli._main import build_parser
        parser = build_parser()
        args = parser.parse_args(["optimize-step", "apply-patch", "--project", "/tmp/p"])
        assert args.optimize_step_command == "apply-patch"

    def test_check_gate_baseline_flag(self) -> None:
        from factory.cli._main import build_parser
        parser = build_parser()
        args = parser.parse_args(["optimize-step", "check-gate", "--project", "/tmp/p", "--baseline"])
        assert args.optimize_step_command == "check-gate"
        assert args.baseline is True

    def test_run_test_subcommand(self) -> None:
        from factory.cli._main import build_parser
        parser = build_parser()
        args = parser.parse_args(["optimize-step", "run-test", "--project", "/tmp/p"])
        assert args.optimize_step_command == "run-test"
