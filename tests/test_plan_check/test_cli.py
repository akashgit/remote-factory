"""Tests for plan-check CLI subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import pytest

from factory.plan_check.cli import add_subcommand, run_plan_check


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    add_subcommand(sub)
    return parser


class TestCliArgsParsing:
    def test_required_project_path(self):
        parser = _make_parser()
        args = parser.parse_args(["plan-check", "/tmp/myproject"])
        assert args.project_path == Path("/tmp/myproject")

    def test_default_values(self):
        parser = _make_parser()
        args = parser.parse_args(["plan-check", "/tmp/myproject"])
        assert args.baseline is None
        assert args.strategy is None
        assert args.output_dir is None
        assert args.report_format == "both"
        assert args.json_stdout is False

    def test_all_optional_args(self):
        parser = _make_parser()
        args = parser.parse_args([
            "plan-check", "/tmp/myproject",
            "--baseline", "abc123",
            "--strategy", "/tmp/plan.md",
            "--output-dir", "/tmp/reports",
            "--format", "json",
            "--json",
        ])
        assert args.baseline == "abc123"
        assert args.strategy == Path("/tmp/plan.md")
        assert args.output_dir == Path("/tmp/reports")
        assert args.report_format == "json"
        assert args.json_stdout is True

    def test_format_choices(self):
        parser = _make_parser()
        for fmt in ("json", "markdown", "both"):
            args = parser.parse_args(["plan-check", "/tmp/p", "--format", fmt])
            assert args.report_format == fmt


class TestCliMissingStrategy:
    def test_exits_2_when_strategy_missing(self, tmp_path: Path):
        strategy = tmp_path / "nonexistent.md"
        with pytest.raises(SystemExit) as exc_info:
            run_plan_check(
                project_path=tmp_path,
                strategy_path=strategy,
                output_dir=tmp_path / "reports",
            )
        assert exc_info.value.code == 2


class TestCliJsonStdout:
    def test_json_stdout(self, tmp_path: Path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        strategy_file = strategy_dir / "current.md"
        strategy_file.write_text(
            "## Plan\n"
            "#### H1: Simple test\n"
            "- **Category:** EXPLORE\n"
            "- **What:**\n"
            "  Create `sample/hello.py`\n"
            "- **Expected impact:** tests 0.0 → 0.5\n"
        )
        (tmp_path / "sample").mkdir()
        (tmp_path / "sample" / "hello.py").write_text("x = 1\n")

        report = run_plan_check(
            project_path=tmp_path,
            strategy_path=strategy_file,
            output_dir=tmp_path / "reports",
            report_format="json",
        )

        from factory.plan_check.reporter import to_json
        json_output = to_json(report)
        parsed = json.loads(json_output)
        assert "all_passed" in parsed
        assert "hypotheses" in parsed
        assert isinstance(parsed["unsatisfied_criteria"], list)
