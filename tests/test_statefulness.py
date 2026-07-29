"""Tests for factory.statefulness — session state consolidation and resume."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from factory.statefulness import (
    _is_stale,
    clear_session_summary,
    generate_session_summary,
    load_summary_if_fresh,
    save_session_summary,
)


@pytest.fixture
def factory_dir(tmp_path: Path) -> Path:
    """Create a minimal .factory/ structure."""
    d = tmp_path / ".factory"
    d.mkdir()
    (d / "state").mkdir()
    (d / "reviews").mkdir()
    (d / "strategy").mkdir()
    return tmp_path


def _write_cycle_state(project: Path, **overrides: object) -> None:
    data = {
        "cycle_id": "abc12345",
        "started_at": "2026-07-28T10:00:00+00:00",
        "mode": "improve",
        "respawns": 1,
        **overrides,
    }
    (project / ".factory" / "state" / "cycle.json").write_text(json.dumps(data))


def _write_checkpoint(project: Path, **overrides: object) -> None:
    data = {
        "mode": "improve",
        "active_experiment_id": 5,
        "completed_agents": ["researcher", "strategist"],
        "pending_agents": ["builder", "health_checker"],
        "last_eval_scores": {"tests": 0.9, "lint": 1.0},
        "current_hypothesis": "Add structured logging",
        "timestamp": "2026-07-28T10:30:00Z",
        **overrides,
    }
    (project / ".factory" / "checkpoint.json").write_text(json.dumps(data))


def _write_events(project: Path, events: list[dict]) -> None:
    lines = [json.dumps(e) for e in events]
    (project / ".factory" / "events.jsonl").write_text("\n".join(lines) + "\n")


def _write_results_tsv(project: Path, rows: list[dict]) -> None:
    import csv

    tsv = project / ".factory" / "results.tsv"
    cols = ["id", "timestamp", "hypothesis", "verdict", "delta"]
    with open(tsv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, dialect="excel-tab")
        w.writeheader()
        for row in rows:
            w.writerow(row)


class TestGenerateSessionSummary:
    def test_empty_factory(self, factory_dir: Path) -> None:
        result = generate_session_summary(factory_dir)
        assert "## Previous Session State" in result
        assert "No prior state found" in result

    def test_partial_state_cycle_only(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        result = generate_session_summary(factory_dir)
        assert "### Cycle State" in result
        assert "Mode: improve" in result
        assert "Cycle ID: abc12345" in result
        assert "### Checkpoint" not in result

    def test_partial_state_checkpoint_only(self, factory_dir: Path) -> None:
        _write_checkpoint(factory_dir)
        result = generate_session_summary(factory_dir)
        assert "### Checkpoint" in result
        assert "researcher, strategist" in result
        assert "builder, health_checker" in result

    def test_full_state(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        _write_checkpoint(factory_dir)
        _write_events(
            factory_dir,
            [
                {
                    "type": "agent.completed",
                    "timestamp": "2026-07-28T10:05:00Z",
                    "agent": "researcher",
                    "data": {"duration_s": 120},
                },
                {
                    "type": "agent.started",
                    "timestamp": "2026-07-28T10:07:00Z",
                    "agent": "strategist",
                    "data": {},
                },
            ],
        )
        (factory_dir / ".factory" / "reviews" / "researcher-latest.md").write_text(
            "## Research Output\nFound 3 similar projects..."
        )
        (factory_dir / ".factory" / "strategy" / "current.md").write_text(
            "## Strategy\n### Hypotheses\n#### H1: Add logging\n- What: structured logs"
        )
        _write_results_tsv(
            factory_dir,
            [
                {
                    "id": "1",
                    "timestamp": "2026-07-28",
                    "hypothesis": "Add tests",
                    "verdict": "keep",
                    "delta": "0.05",
                },
            ],
        )

        result = generate_session_summary(factory_dir)
        assert "### Cycle State" in result
        assert "### Checkpoint" in result
        assert "### Agent Timeline" in result
        assert "researcher: completed (120s)" in result
        assert "### Review Summaries" in result
        assert "### Strategy" in result
        assert "### Recent Experiments" in result

    def test_corrupt_events_graceful(self, factory_dir: Path) -> None:
        (factory_dir / ".factory" / "events.jsonl").write_text("not valid json\n{bad\n")
        result = generate_session_summary(factory_dir)
        assert "## Previous Session State" in result

    def test_corrupt_checkpoint_graceful(self, factory_dir: Path) -> None:
        (factory_dir / ".factory" / "checkpoint.json").write_text("{invalid")
        result = generate_session_summary(factory_dir)
        assert "## Previous Session State" in result
        assert "### Checkpoint" not in result


class TestSaveSessionSummary:
    def test_atomic_write(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        save_session_summary(factory_dir)

        summary_path = factory_dir / ".factory" / "state" / "session_summary.md"
        assert summary_path.exists()
        content = summary_path.read_text()
        assert "## Previous Session State" in content

        # No leftover .tmp files
        tmp_files = list((factory_dir / ".factory" / "state").glob("session_*.tmp"))
        assert tmp_files == []

    def test_char_cap_enforcement(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        (factory_dir / ".factory" / "strategy" / "current.md").write_text("x" * 3000)
        _write_checkpoint(factory_dir)
        _write_events(
            factory_dir,
            [
                {
                    "type": "agent.completed",
                    "timestamp": f"2026-07-28T10:{i:02d}:00Z",
                    "agent": f"agent_{i}",
                    "data": {"duration_s": i * 10},
                }
                for i in range(20)
            ],
        )
        # Create many large review files to push total well over 2000 chars
        for i in range(20):
            (factory_dir / ".factory" / "reviews" / f"review-{i}.md").write_text(
                f"## Review {i}\n" + "detailed findings " * 20
            )
        _write_results_tsv(
            factory_dir,
            [
                {
                    "id": str(i),
                    "timestamp": "2026-07-28",
                    "hypothesis": f"Hypothesis {i} " * 5,
                    "verdict": "keep",
                    "delta": "0.05",
                }
                for i in range(10)
            ],
        )

        save_session_summary(factory_dir)

        summary_path = factory_dir / ".factory" / "state" / "session_summary.md"
        content = summary_path.read_text()
        assert len(content) <= 2000
        assert "[truncated]" in content

    def test_never_raises_on_bad_path(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "nonexistent" / "deep" / "path"
        # Should not raise even with a deeply nested nonexistent path
        save_session_summary(bad_path)


class TestClearSessionSummary:
    def test_clear_existing(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        save_session_summary(factory_dir)
        assert clear_session_summary(factory_dir) is True
        assert not (factory_dir / ".factory" / "state" / "session_summary.md").exists()

    def test_clear_nonexistent(self, factory_dir: Path) -> None:
        assert clear_session_summary(factory_dir) is False


class TestIsStale:
    def test_fresh_file(self, factory_dir: Path) -> None:
        p = factory_dir / ".factory" / "state" / "test.md"
        p.write_text("hello")
        assert _is_stale(p) is False

    def test_old_file(self, factory_dir: Path) -> None:
        p = factory_dir / ".factory" / "state" / "test.md"
        p.write_text("hello")
        old_mtime = time.time() - (25 * 3600)
        os.utime(p, (old_mtime, old_mtime))
        assert _is_stale(p) is True

    def test_missing_file(self, factory_dir: Path) -> None:
        p = factory_dir / ".factory" / "state" / "nonexistent.md"
        assert _is_stale(p) is True


class TestLoadSummaryIfFresh:
    def test_loads_fresh_summary(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        save_session_summary(factory_dir)
        result = load_summary_if_fresh(factory_dir)
        assert result is not None
        assert "## Previous Session State" in result

    def test_returns_none_when_stale(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir)
        save_session_summary(factory_dir)
        summary_path = factory_dir / ".factory" / "state" / "session_summary.md"
        old_mtime = time.time() - (25 * 3600)
        os.utime(summary_path, (old_mtime, old_mtime))
        assert load_summary_if_fresh(factory_dir) is None

    def test_returns_none_when_missing(self, factory_dir: Path) -> None:
        assert load_summary_if_fresh(factory_dir) is None

    def test_cycle_id_match(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir, cycle_id="abc12345")
        save_session_summary(factory_dir)
        result = load_summary_if_fresh(factory_dir, cycle_id="abc12345")
        assert result is not None

    def test_cycle_id_mismatch(self, factory_dir: Path) -> None:
        _write_cycle_state(factory_dir, cycle_id="abc12345")
        save_session_summary(factory_dir)
        result = load_summary_if_fresh(factory_dir, cycle_id="different_id")
        assert result is None
