"""Tests for factory.refine_state — refinement tracking and regrounding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.models import RefinementEntry, RefinementState
from factory.refine_state import (
    begin_refinement,
    complete_refinement,
    format_begin,
    format_status,
    read_state,
)


# ── model validation ─────────────────────────────────────────────


class TestRefinementEntryModel:
    def test_valid_entry(self) -> None:
        entry = RefinementEntry(
            sequence=1,
            request="fix typo in README",
            started_at="2026-05-24T12:00:00Z",
        )
        assert entry.sequence == 1
        assert entry.request == "fix typo in README"
        assert entry.completed_at is None
        assert entry.verdict is None

    def test_valid_entry_completed(self) -> None:
        entry = RefinementEntry(
            sequence=2,
            request="add --verbose flag",
            started_at="2026-05-24T12:00:00Z",
            completed_at="2026-05-24T12:05:00Z",
            verdict="keep",
        )
        assert entry.verdict == "keep"
        assert entry.completed_at is not None

    def test_strict_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            RefinementEntry(
                sequence=1,
                request="test",
                started_at="2026-05-24T12:00:00Z",
                bogus_field="oops",
            )

    def test_defaults(self) -> None:
        entry = RefinementEntry(
            sequence=1, request="test", started_at="2026-05-24T12:00:00Z"
        )
        assert entry.completed_at is None
        assert entry.verdict is None


class TestRefinementStateModel:
    def test_empty_state(self) -> None:
        state = RefinementState()
        assert state.entries == []

    def test_state_with_entries(self) -> None:
        state = RefinementState(
            entries=[
                RefinementEntry(
                    sequence=1,
                    request="fix typo",
                    started_at="2026-05-24T12:00:00Z",
                    verdict="keep",
                ),
            ]
        )
        assert len(state.entries) == 1

    def test_strict_rejects_extras(self) -> None:
        with pytest.raises(ValidationError):
            RefinementState(entries=[], extra_field="nope")


# ── read_state ───────────────────────────────────────────────────


class TestReadState:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        state = read_state(tmp_path)
        assert state.entries == []

    def test_reads_existing_file(self, tmp_path: Path) -> None:
        state_dir = tmp_path / ".factory" / "state"
        state_dir.mkdir(parents=True)
        data = {
            "entries": [
                {
                    "sequence": 1,
                    "request": "fix typo",
                    "started_at": "2026-05-24T12:00:00Z",
                    "completed_at": None,
                    "verdict": None,
                }
            ]
        }
        (state_dir / "refinements.json").write_text(json.dumps(data))
        state = read_state(tmp_path)
        assert len(state.entries) == 1
        assert state.entries[0].request == "fix typo"


# ── begin_refinement ─────────────────────────────────────────────


class TestBeginRefinement:
    def test_creates_file_and_entry(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        entry = begin_refinement(tmp_path, "fix typo in README")
        assert entry.sequence == 1
        assert entry.request == "fix typo in README"
        assert entry.started_at is not None

        state = read_state(tmp_path)
        assert len(state.entries) == 1

    def test_appends_entries(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        begin_refinement(tmp_path, "first change")
        entry2 = begin_refinement(tmp_path, "second change")
        assert entry2.sequence == 2

        state = read_state(tmp_path)
        assert len(state.entries) == 2
        assert state.entries[0].request == "first change"
        assert state.entries[1].request == "second change"

    def test_returns_correct_sequence_numbers(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        for i in range(5):
            entry = begin_refinement(tmp_path, f"change {i}")
            assert entry.sequence == i + 1


# ── complete_refinement ──────────────────────────────────────────


class TestCompleteRefinement:
    def test_updates_last_entry(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        begin_refinement(tmp_path, "fix typo")
        complete_refinement(tmp_path, "keep")

        state = read_state(tmp_path)
        assert state.entries[0].verdict == "keep"
        assert state.entries[0].completed_at is not None

    def test_no_entries_is_noop(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        complete_refinement(tmp_path, "keep")
        state = read_state(tmp_path)
        assert state.entries == []

    def test_multiple_entries_updates_last(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()
        begin_refinement(tmp_path, "first")
        complete_refinement(tmp_path, "keep")
        begin_refinement(tmp_path, "second")
        complete_refinement(tmp_path, "revert")

        state = read_state(tmp_path)
        assert state.entries[0].verdict == "keep"
        assert state.entries[1].verdict == "revert"


# ── format_status ────────────────────────────────────────────────


class TestFormatStatus:
    def test_empty_state_contains_regrounding(self) -> None:
        output = format_status(RefinementState())
        assert "Factory CEO" in output
        assert "Sacred Rule 8" in output
        assert "No refinements recorded" in output

    def test_with_entries_shows_history(self) -> None:
        state = RefinementState(
            entries=[
                RefinementEntry(
                    sequence=1,
                    request="fix typo",
                    started_at="2026-05-24T12:00:00Z",
                    verdict="keep",
                ),
                RefinementEntry(
                    sequence=2,
                    request="add flag",
                    started_at="2026-05-24T12:05:00Z",
                ),
            ]
        )
        output = format_status(state)
        assert "Refinements recorded: 2" in output
        assert '"fix typo" → KEEP' in output
        assert '"add flag" → IN PROGRESS' in output
        assert "REMINDER" in output

    def test_contains_identity_reanchor(self) -> None:
        output = format_status(RefinementState())
        assert "CEO IDENTITY RE-ANCHOR" in output


# ── format_begin ─────────────────────────────────────────────────


class TestFormatBegin:
    def test_basic_output(self) -> None:
        entry = RefinementEntry(
            sequence=1,
            request="fix typo",
            started_at="2026-05-24T12:00:00Z",
        )
        output = format_begin(entry)
        assert 'Refinement #1 registered: "fix typo"' in output
        assert "spawn the Refiner agent" in output
        assert "Advisory" not in output

    def test_advisory_at_5(self) -> None:
        entry = RefinementEntry(
            sequence=5,
            request="fifth change",
            started_at="2026-05-24T12:00:00Z",
        )
        output = format_begin(entry)
        assert "Advisory" in output
        assert "refinement #5" in output
        assert "quality degradation" in output

    def test_stronger_advisory_at_10(self) -> None:
        entry = RefinementEntry(
            sequence=10,
            request="tenth change",
            started_at="2026-05-24T12:00:00Z",
        )
        output = format_begin(entry)
        assert "Advisory" in output
        assert "refinement #10" in output
        assert "Consider starting a fresh session" in output

    def test_contains_identity_reanchor(self) -> None:
        entry = RefinementEntry(
            sequence=1,
            request="test",
            started_at="2026-05-24T12:00:00Z",
        )
        output = format_begin(entry)
        assert "CEO IDENTITY RE-ANCHOR" in output


# ── CLI integration ──────────────────────────────────────────────


class TestCLIIntegration:
    def test_refine_status_registered(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["refine-status", "/tmp/test-project"])
        assert args.command == "refine-status"
        assert args.path == "/tmp/test-project"

    def test_refine_begin_registered(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "refine-begin", "/tmp/test-project",
            "--request", "fix the typo",
        ])
        assert args.command == "refine-begin"
        assert args.request == "fix the typo"

    def test_refine_complete_registered(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "refine-complete", "/tmp/test-project",
            "--verdict", "keep",
        ])
        assert args.command == "refine-complete"
        assert args.verdict == "keep"

    def test_refine_status_callable(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refine_status
        import argparse

        (tmp_path / ".factory").mkdir()
        ns = argparse.Namespace(path=str(tmp_path))
        result = cmd_refine_status(ns)
        assert result == 0

    def test_refine_begin_callable(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refine_begin
        import argparse

        (tmp_path / ".factory").mkdir()
        ns = argparse.Namespace(path=str(tmp_path), request="test change")
        result = cmd_refine_begin(ns)
        assert result == 0

        state = read_state(tmp_path)
        assert len(state.entries) == 1

    def test_refine_complete_callable(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refine_begin, cmd_refine_complete
        import argparse

        (tmp_path / ".factory").mkdir()
        cmd_refine_begin(argparse.Namespace(path=str(tmp_path), request="test"))
        result = cmd_refine_complete(
            argparse.Namespace(path=str(tmp_path), verdict="keep")
        )
        assert result == 0

        state = read_state(tmp_path)
        assert state.entries[0].verdict == "keep"

    def test_refine_complete_no_entries_returns_error(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refine_complete
        import argparse

        (tmp_path / ".factory").mkdir()
        result = cmd_refine_complete(
            argparse.Namespace(path=str(tmp_path), verdict="keep")
        )
        assert result == 1

    def test_handlers_in_dispatch(self) -> None:
        from factory.cli import build_parser
        parser = build_parser()
        for cmd in ["refine-status", "refine-begin", "refine-complete"]:
            # Should not raise
            args = parser.parse_args([cmd, "/tmp/test"] if cmd == "refine-status"
                                     else [cmd, "/tmp/test", "--request", "x"] if cmd == "refine-begin"
                                     else [cmd, "/tmp/test", "--verdict", "keep"])
            assert args.command == cmd
