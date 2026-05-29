"""Tests for factory.handoff module."""

from __future__ import annotations

import json
from pathlib import Path

from factory.handoff import generate_handoff


def test_generate_handoff_empty_project(tmp_path: Path) -> None:
    """Handoff should not crash when .factory/ is missing or empty."""
    brief = generate_handoff(tmp_path)
    assert "# Handoff Brief:" in brief
    assert "## Current State" in brief
    assert "## Score Trajectory" in brief
    assert "## What's In Progress" in brief
    assert "## What's Pending" in brief
    assert "## Recent Activity" in brief
    assert "## Recommended Next Steps" in brief


def test_generate_handoff_with_data(tmp_path: Path) -> None:
    """Handoff should include data from populated .factory/ sources."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    # Config
    config = {"goal": "Build a weather CLI", "eval_command": "pytest", "eval_threshold": 0.8}
    (factory_dir / "config.json").write_text(json.dumps(config))

    # Checkpoint
    checkpoint = {
        "mode": "improve",
        "active_experiment_id": 3,
        "current_hypothesis": "add caching layer",
        "completed_agents": ["researcher", "strategist"],
        "pending_agents": ["builder", "reviewer"],
        "timestamp": "2026-05-25T10:00:00",
    }
    (factory_dir / "checkpoint.json").write_text(json.dumps(checkpoint))

    # Results TSV
    tsv = "id\ttimestamp\thypothesis\tchange_summary\tscore_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\n"
    tsv += "1\t2026-05-25T09:00:00\tadd tests\tfixed tests\t0.500\t0.600\t0.100\tkeep\t0.50\t\n"
    tsv += "2\t2026-05-25T09:30:00\tadd lint\tfixed lint\t0.600\t0.700\t0.100\trevert\t0.30\t\n"
    (factory_dir / "results.tsv").write_text(tsv)

    # Backlog
    strategy_dir = factory_dir / "strategy"
    strategy_dir.mkdir()
    (strategy_dir / "backlog.md").write_text("- Add error handling\n- Improve logging\n- Add docs\n")
    (strategy_dir / "current.md").write_text("## Current Strategy\n\nFocus on reliability.\n")

    # Events
    events = [
        json.dumps({"timestamp": "2026-05-25T09:00:00", "type": "experiment.begin"}),
        json.dumps({"timestamp": "2026-05-25T09:30:00", "type": "experiment.finalize"}),
    ]
    (factory_dir / "events.jsonl").write_text("\n".join(events) + "\n")

    # Reviews
    reviews_dir = factory_dir / "reviews"
    reviews_dir.mkdir()
    (reviews_dir / "researcher-latest.md").write_text("Research findings here")

    brief = generate_handoff(tmp_path)

    assert "Build a weather CLI" in brief
    assert "improve" in brief
    assert "add caching layer" in brief
    assert "builder, reviewer" in brief
    assert "Total experiments:** 2" in brief
    assert "Kept:** 1" in brief
    assert "Reverted:** 1" in brief
    assert "0.700" in brief
    assert "Backlog items:** 3" in brief
    assert "Add error handling" in brief
    assert "experiment.begin" in brief
    assert "researcher" in brief


def test_generate_handoff_missing_individual_files(tmp_path: Path) -> None:
    """Each section degrades gracefully when its source file is missing."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    # Only provide config, everything else missing
    config = {"goal": "Test project", "eval_command": "echo ok", "eval_threshold": 0.5}
    (factory_dir / "config.json").write_text(json.dumps(config))

    brief = generate_handoff(tmp_path)

    assert "Test project" in brief
    assert "No checkpoint found" in brief
    assert "No experiment history" in brief
    assert "No backlog file" in brief
    assert "No events log" in brief
    assert "No checkpoint; start a new cycle" in brief


def test_cli_handoff_subcommand(tmp_path: Path) -> None:
    """The CLI subparser for handoff is correctly wired."""
    from factory.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["handoff", str(tmp_path)])
    assert args.command == "handoff"
    assert args.path == str(tmp_path)
