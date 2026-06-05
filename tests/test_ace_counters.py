"""Tests for ACE playbook counter wiring — helpful/harmful from experiment verdicts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from factory.ace.curator import curate_playbook
from factory.ace.models import Playbook, PlaybookItem
from factory.ace.reflector import (
    _extract_key_terms,
    _hypothesis_matches_bullet,
    update_counters_from_experiments,
    update_playbook_counters,
)
from factory.cli import cmd_ace_stats
from factory.models import ExperimentRecord


# ── helpers ─────────────────────────────────────────────────────


def _make_record(
    id: int,
    hypothesis: str,
    verdict: str = "keep",
    delta: float | None = None,
    notes: str = "",
) -> ExperimentRecord:
    return ExperimentRecord(
        id=id,
        timestamp=datetime.now(),
        hypothesis=hypothesis,
        change_summary="",
        issue_number=None,
        pr_number=None,
        score_before=None,
        score_after=None,
        delta=delta,
        verdict=verdict,
        cost_usd=None,
        notes=notes,
    )


# ── Fuzzy matching ─────────────────────────────────────────────


class TestFuzzyMatching:
    def test_extract_key_terms(self):
        terms = _extract_key_terms("Add logging coverage to the parser module")
        assert "logging" in terms
        assert "coverage" in terms
        assert "parser" in terms
        assert "module" in terms
        # Stop words should be excluded
        assert "the" not in terms
        assert "to" not in terms

    def test_hypothesis_matches_bullet_by_terms(self):
        hyp = "Prioritize bugfix hypotheses for parser reliability"
        bullet = "Prioritize bugfix hypotheses — 5/5 kept (100% success rate)"
        assert _hypothesis_matches_bullet(hyp, bullet)

    def test_hypothesis_no_match(self):
        hyp = "Refactor the database layer"
        bullet = "Prioritize logging hypotheses — 5/5 kept"
        assert not _hypothesis_matches_bullet(hyp, bullet)

    def test_empty_hypothesis(self):
        assert not _hypothesis_matches_bullet("", "Some bullet content")

    def test_short_words_ignored(self):
        terms = _extract_key_terms("I am OK")
        # All short or stop words
        assert terms == []


# ── Counter increment on keep ──────────────────────────────────


class TestCounterIncrementKeep:
    def test_keep_increments_helpful(self):
        playbook = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Prioritize bugfix hypotheses for better outcomes",
                helpful=0,
                harmful=0,
                section="DO",
            ),
        ])
        records = [
            _make_record(1, "Fix bug in the bugfix handler", verdict="keep"),
        ]
        updated = update_playbook_counters(playbook, records)
        assert updated.items[0].helpful == 1
        assert updated.items[0].harmful == 0

    def test_multiple_keeps_accumulate(self):
        playbook = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Prioritize bugfix hypotheses for better outcomes",
                helpful=0,
                harmful=0,
                section="DO",
            ),
        ])
        records = [
            _make_record(1, "Fix bug in the bugfix handler", verdict="keep"),
            _make_record(2, "Another bugfix for the parser bugfix", verdict="keep"),
        ]
        updated = update_playbook_counters(playbook, records)
        assert updated.items[0].helpful == 2
        assert updated.items[0].harmful == 0


# ── Counter increment on revert ────────────────────────────────


class TestCounterIncrementRevert:
    def test_revert_increments_harmful(self):
        playbook = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Prioritize bugfix hypotheses for better outcomes",
                helpful=0,
                harmful=0,
                section="DO",
            ),
        ])
        records = [
            _make_record(1, "Fix bug in the bugfix handler", verdict="revert"),
        ]
        updated = update_playbook_counters(playbook, records)
        assert updated.items[0].helpful == 0
        assert updated.items[0].harmful == 1

    def test_mixed_verdicts(self):
        playbook = Playbook(role="builder", items=[
            PlaybookItem(
                id="build-00001",
                content="Keep changes small and focused for better outcomes",
                helpful=0,
                harmful=0,
                section="DO",
            ),
        ])
        records = [
            _make_record(1, "Small focused change to keep things clean", verdict="keep"),
            _make_record(2, "Another small focused improvement", verdict="keep"),
            _make_record(3, "Small focused refactor that failed", verdict="revert"),
        ]
        updated = update_playbook_counters(playbook, records)
        assert updated.items[0].helpful == 2
        assert updated.items[0].harmful == 1


# ── Pruning threshold ──────────────────────────────────────────


class TestPruningThreshold:
    def test_prune_when_harmful_exceeds_helpful_by_3(self):
        """Bullets where harmful - helpful >= 3 should be pruned."""
        existing = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Good rule that works",
                helpful=5,
                harmful=0,
                section="DO",
            ),
            PlaybookItem(
                id="strat-00002",
                content="Bad rule that consistently fails",
                helpful=0,
                harmful=3,
                section="DO",
            ),
        ])
        updated = curate_playbook(existing, [])
        assert len(updated.items) == 1
        assert updated.items[0].content == "Good rule that works"

    def test_no_prune_when_margin_below_threshold(self):
        """Bullets where harmful - helpful < 3 should NOT be pruned."""
        existing = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Slightly bad rule",
                helpful=0,
                harmful=2,
                section="DO",
            ),
        ])
        updated = curate_playbook(existing, [])
        assert len(updated.items) == 1

    def test_prune_with_mixed_counters(self):
        """harmful=5, helpful=2 -> margin=3, should be pruned."""
        existing = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Rule with mixed signal",
                helpful=2,
                harmful=5,
                section="DO",
            ),
        ])
        updated = curate_playbook(existing, [])
        assert len(updated.items) == 0

    def test_no_prune_when_margin_is_2(self):
        """harmful=4, helpful=2 -> margin=2, should NOT be pruned by the 3+ rule."""
        existing = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Borderline rule with some issues",
                helpful=2,
                harmful=4,
                section="DO",
            ),
        ])
        # The legacy rule (harmful > helpful and total >= 3) would prune this
        # since harmful(4) > helpful(2) and total(6) >= 3
        updated = curate_playbook(existing, [])
        assert len(updated.items) == 0


# ── Counter persistence ───────────────────────────────────────


class TestCounterPersistence:
    def test_write_and_read_back(self, tmp_path: Path):
        """Counters should survive write-to-disk then read-back."""
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()

        # Write initial playbook
        playbook = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Prioritize bugfix hypotheses for reliable outcomes",
                helpful=0,
                harmful=0,
                section="DO",
            ),
        ])
        playbook_path = playbooks_dir / "strategist.md"
        playbook_path.write_text(playbook.to_markdown())

        # Update counters with experiment data
        records = [
            _make_record(1, "Fix bug in bugfix handler for reliability", verdict="keep"),
            _make_record(2, "Another bugfix to improve reliability", verdict="revert"),
        ]
        update_counters_from_experiments(playbooks_dir, records)

        # Read back and verify
        reloaded = Playbook.from_markdown(playbook_path.read_text())
        assert reloaded.items[0].helpful == 1
        assert reloaded.items[0].harmful == 1

    def test_no_write_when_no_changes(self, tmp_path: Path):
        """If no records match any bullet, the file should not be rewritten."""
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()

        playbook = Playbook(role="strategist", items=[
            PlaybookItem(
                id="strat-00001",
                content="Prioritize bugfix hypotheses",
                helpful=3,
                harmful=1,
                section="DO",
            ),
        ])
        playbook_path = playbooks_dir / "strategist.md"
        playbook_path.write_text(playbook.to_markdown())
        original_mtime = playbook_path.stat().st_mtime

        # Records that don't match any bullet
        records = [
            _make_record(1, "Completely unrelated database migration", verdict="keep"),
        ]

        import time
        time.sleep(0.01)  # ensure mtime would differ if written
        update_counters_from_experiments(playbooks_dir, records)

        # mtime should not change
        assert playbook_path.stat().st_mtime == original_mtime

    def test_error_verdict_ignored(self, tmp_path: Path):
        """Error verdicts should not increment any counter."""
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()

        playbook = Playbook(role="builder", items=[
            PlaybookItem(
                id="build-00001",
                content="Keep changes small and focused for builder",
                helpful=0,
                harmful=0,
                section="DO",
            ),
        ])
        playbook_path = playbooks_dir / "builder.md"
        playbook_path.write_text(playbook.to_markdown())

        records = [
            _make_record(1, "Small focused changes for builder", verdict="error"),
        ]
        update_counters_from_experiments(playbooks_dir, records)

        reloaded = Playbook.from_markdown(playbook_path.read_text())
        assert reloaded.items[0].helpful == 0
        assert reloaded.items[0].harmful == 0


# ── cmd_ace_stats output format ────────────────────────────────


class TestCmdAceStats:
    def test_output_format(self, capsys, tmp_path, monkeypatch):
        """ace-stats should print a formatted table with header and summary."""
        import argparse

        from factory.ace.models import Playbook, PlaybookItem

        # Set up user-local playbooks with known content
        user_dir = tmp_path / "playbooks"
        user_dir.mkdir()
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(user_dir))
        playbook = Playbook(role="ceo", items=[
            PlaybookItem(id="ceo-00001", content="Test rule", helpful=5, harmful=1, section="DO"),
        ])
        (user_dir / "ceo.md").write_text(playbook.to_markdown())

        args = argparse.Namespace()
        result = cmd_ace_stats(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Role" in captured.out
        assert "ID" in captured.out
        assert "helpful" in captured.out
        assert "harmful" in captured.out
        assert "net" in captured.out
        assert "Total:" in captured.out
        assert "bullets" in captured.out

    def test_empty_user_dir_falls_back_to_defaults(self, capsys, tmp_path, monkeypatch):
        """ace-stats should fall back to factory defaults when no user playbooks exist."""
        import argparse

        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "empty"))
        (tmp_path / "empty").mkdir()

        args = argparse.Namespace()
        result = cmd_ace_stats(args)
        assert result == 0

        captured = capsys.readouterr()
        assert "Total:" in captured.out
