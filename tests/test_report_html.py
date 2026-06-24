"""Tests for factory.report_html — HTML experiment report rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.report_html import generate_experiment_report


@pytest.fixture()
def experiment_dir(tmp_path: Path) -> Path:
    """Create a minimal .factory/experiments/001/ structure."""
    exp_dir = tmp_path / ".factory" / "experiments" / "001"
    exp_dir.mkdir(parents=True)

    (exp_dir / "hypothesis.md").write_text("## H1: Improve logging\nAdd structured logging.")

    (exp_dir / "eval_before.json").write_text(json.dumps({
        "results": [
            {"name": "test_coverage", "score": 0.6},
            {"name": "lint_score", "score": 0.8},
        ],
        "composite": 0.7,
    }))

    (exp_dir / "eval_after.json").write_text(json.dumps({
        "results": [
            {"name": "test_coverage", "score": 0.75},
            {"name": "lint_score", "score": 0.85},
        ],
        "composite": 0.8,
    }))

    (exp_dir / "changes.diff").write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,3 +1,5 @@\n"
        "+import structlog\n"
        "+log = structlog.get_logger()\n"
        " def main():\n"
        "-    print('hello')\n"
        "+    log.info('hello')\n"
    )

    (exp_dir / "verdict.json").write_text(json.dumps({
        "verdict": "keep",
        "rationale": "Score improved across all dimensions.",
        "date": "2026-06-24",
    }))

    return tmp_path


def test_generate_report_valid(experiment_dir: Path) -> None:
    """Test report generation with valid experiment data."""
    output = generate_experiment_report(experiment_dir, "001")

    assert output.exists()
    assert output.name == "experiment-001.html"
    assert output.parent.name == "reports"

    html = output.read_text()
    assert "<!DOCTYPE html>" in html
    assert "Experiment #001" in html


def test_report_contains_expected_sections(experiment_dir: Path) -> None:
    """Test HTML contains all expected sections."""
    output = generate_experiment_report(experiment_dir, "001")
    html = output.read_text()

    assert "Hypothesis" in html
    assert "Eval Results" in html
    assert "Changes" in html
    assert "Verdict Rationale" in html
    assert "KEEP" in html
    assert "test_coverage" in html
    assert "lint_score" in html


def test_report_verdict_badge_keep(experiment_dir: Path) -> None:
    """Test KEEP verdict produces green badge."""
    output = generate_experiment_report(experiment_dir, "001")
    html = output.read_text()

    assert "badge-keep" in html
    assert "KEEP" in html


def test_report_verdict_badge_revert(experiment_dir: Path) -> None:
    """Test REVERT verdict produces red badge."""
    verdict_path = experiment_dir / ".factory" / "experiments" / "001" / "verdict.json"
    verdict_path.write_text(json.dumps({
        "verdict": "revert",
        "rationale": "Score regressed.",
        "date": "2026-06-24",
    }))

    output = generate_experiment_report(experiment_dir, "001")
    html = output.read_text()

    assert "badge-revert" in html
    assert "REVERT" in html


def test_missing_experiment_raises_error(tmp_path: Path) -> None:
    """Test that a missing experiment directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Experiment directory not found"):
        generate_experiment_report(tmp_path, "999")


def test_autoescaping_prevents_xss(experiment_dir: Path) -> None:
    """Test that HTML in hypothesis text is escaped."""
    xss_path = experiment_dir / ".factory" / "experiments" / "001" / "hypothesis.md"
    xss_path.write_text('<script>alert("xss")</script>')

    output = generate_experiment_report(experiment_dir, "001")
    html = output.read_text()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_custom_output_path(experiment_dir: Path) -> None:
    """Test writing report to a custom output path."""
    custom_path = experiment_dir / "custom" / "report.html"
    output = generate_experiment_report(experiment_dir, "001", output_path=custom_path)

    assert output == custom_path
    assert output.exists()
    html = output.read_text()
    assert "Experiment #001" in html


def test_report_with_missing_optional_files(tmp_path: Path) -> None:
    """Test report generation when optional files are missing."""
    exp_dir = tmp_path / ".factory" / "experiments" / "002"
    exp_dir.mkdir(parents=True)
    (exp_dir / "hypothesis.md").write_text("Simple hypothesis")
    (exp_dir / "eval_before.json").write_text("{}")
    (exp_dir / "eval_after.json").write_text("{}")
    (exp_dir / "verdict.json").write_text("{}")

    output = generate_experiment_report(tmp_path, "002")
    html = output.read_text()

    assert "Experiment #002" in html
    assert "PENDING" in html


def test_reporter_role_resolves() -> None:
    """Test that 'reporter' role resolves to a prompt via resolve_prompt."""
    from factory.agents.runner import resolve_prompt

    prompt = resolve_prompt("reporter")
    assert "Reporter" in prompt
    assert "HTML" in prompt
