"""Tests for the ACE self-improvement system (factory/ace/)."""

import csv
from datetime import datetime
from pathlib import Path

from factory.ace.curator import curate_playbook
from factory.ace.injector import inject_playbook, load_playbook
from factory.ace.models import Playbook, PlaybookItem
from factory.ace.reflector import (
    _category_stats,
    _detect_repetition,
    _strategist_bullets,
    reflect_on_experiments,
)
from factory.models import ExperimentRecord


# ── helpers ─────────────────────────────────────────────────────


def _make_record(
    id: int,
    hypothesis: str,
    verdict: str = "keep",
    delta: float | None = None,
    change_summary: str = "",
) -> ExperimentRecord:
    return ExperimentRecord(
        id=id,
        timestamp=datetime.now(),
        hypothesis=hypothesis,
        change_summary=change_summary,
        issue_number=None,
        pr_number=None,
        score_before=None,
        score_after=None,
        delta=delta,
        verdict=verdict,
        cost_usd=None,
        notes="",
    )


def _write_tsv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, dialect="excel-tab")
        writer.writeheader()
        for row in rows:
            full = {k: row.get(k, "") for k in fieldnames}
            if not full["timestamp"]:
                full["timestamp"] = datetime.now().isoformat()
            if not full["notes"]:
                full["notes"] = ""
            if not full["change_summary"]:
                full["change_summary"] = ""
            writer.writerow(full)


# ── PlaybookItem ────────────────────────────────────────────────


class TestPlaybookItem:
    def test_to_line(self):
        item = PlaybookItem(id="strat-00001", content="Test rule", helpful=5, harmful=2)
        line = item.to_line()
        assert "[strat-00001]" in line
        assert "helpful=5" in line
        assert "harmful=2" in line
        assert "Test rule" in line

    def test_from_line_roundtrip(self):
        item = PlaybookItem(id="strat-00001", content="Test rule", helpful=5, harmful=2)
        parsed = PlaybookItem.from_line(item.to_line())
        assert parsed is not None
        assert parsed.id == "strat-00001"
        assert parsed.content == "Test rule"
        assert parsed.helpful == 5
        assert parsed.harmful == 2

    def test_from_line_invalid(self):
        assert PlaybookItem.from_line("not a valid line") is None
        assert PlaybookItem.from_line("") is None

    def test_net_score(self):
        item = PlaybookItem(id="x", content="y", helpful=10, harmful=3)
        assert item.net_score == 7

    def test_net_score_negative(self):
        item = PlaybookItem(id="x", content="y", helpful=1, harmful=5)
        assert item.net_score == -4


# ── Playbook ───────────────────────────────────────────────────


class TestPlaybook:
    def test_empty_playbook(self):
        pb = Playbook.empty("strategist")
        assert pb.role == "strategist"
        assert pb.items == []

    def test_to_markdown_and_back(self):
        items = [
            PlaybookItem(id="strat-00001", content="Do this", helpful=5, harmful=1, section="DO"),
            PlaybookItem(id="strat-00002", content="Avoid that", helpful=1, harmful=8, section="DON'T"),
        ]
        pb = Playbook(role="strategist", items=items)
        md = pb.to_markdown()

        parsed = Playbook.from_markdown(md)
        assert parsed.role == "strategist"
        assert len(parsed.items) == 2

        do_items = [i for i in parsed.items if i.section == "DO"]
        dont_items = [i for i in parsed.items if i.section == "DON'T"]
        assert len(do_items) == 1
        assert len(dont_items) == 1
        assert do_items[0].content == "Do this"
        assert dont_items[0].content == "Avoid that"

    def test_markdown_contains_frontmatter(self):
        pb = Playbook(role="builder", items=[])
        md = pb.to_markdown()
        assert "role: builder" in md
        assert "item_count: 0" in md

    def test_from_markdown_missing_sections(self):
        md = "---\nrole: test\n---\n\nsome content"
        pb = Playbook.from_markdown(md)
        assert pb.role == "test"
        assert pb.items == []

    def test_sorted_by_net_score(self):
        items = [
            PlaybookItem(id="a", content="low", helpful=1, harmful=0, section="DO"),
            PlaybookItem(id="b", content="high", helpful=10, harmful=0, section="DO"),
            PlaybookItem(id="c", content="mid", helpful=5, harmful=0, section="DO"),
        ]
        pb = Playbook(role="test", items=items)
        md = pb.to_markdown()
        high_pos = md.index("high")
        mid_pos = md.index("mid")
        low_pos = md.index("low")
        assert high_pos < mid_pos < low_pos


# ── Reflector ──────────────────────────────────────────────────


class TestCategoryStats:
    def test_basic_stats(self):
        outcomes = [
            ("bugfix", "keep", 0.01),
            ("bugfix", "keep", 0.02),
            ("bugfix", "revert", -0.01),
            ("feature", "keep", 0.05),
        ]
        stats = _category_stats(outcomes)
        assert stats["bugfix"]["total"] == 3
        assert stats["bugfix"]["kept"] == 2
        assert stats["bugfix"]["reverted"] == 1
        assert abs(stats["bugfix"]["rate"] - 2 / 3) < 0.01

    def test_empty_outcomes(self):
        assert _category_stats([]) == {}


class TestDetectRepetition:
    def test_no_repetition(self):
        records = [
            _make_record(i, h) for i, h in enumerate([
                "Fix bug in parser",
                "Add logging to store",
                "Improve test coverage",
                "Add new endpoint",
                "Refactor CLI",
            ])
        ]
        assert _detect_repetition(records) == []

    def test_detects_dominance(self):
        records = [_make_record(i, f"Fix bug #{i}") for i in range(5)]
        repeated = _detect_repetition(records)
        assert "bugfix" in repeated


class TestStrategistBullets:
    def test_high_keep_category_produces_do(self):
        outcomes = [("observability", "keep", 0.01)] * 6
        records = [_make_record(i, "Add logging", verdict="keep") for i in range(6)]
        bullets = _strategist_bullets(outcomes, records)
        do_bullets = [b for b in bullets if b.section == "DO"]
        assert any("observability" in b.content.lower() for b in do_bullets)

    def test_low_keep_category_produces_dont(self):
        outcomes = [("refactoring", "revert", -0.02)] * 5 + [("refactoring", "keep", 0.01)]
        records = [
            _make_record(i, "Refactor module", verdict="revert" if i < 5 else "keep")
            for i in range(6)
        ]
        bullets = _strategist_bullets(outcomes, records)
        dont_bullets = [b for b in bullets if b.section == "DON'T"]
        assert any("refactoring" in b.content.lower() for b in dont_bullets)

    def test_empty_data_returns_empty(self):
        assert _strategist_bullets([], []) == []


class TestReflectOnExperiments:
    def test_no_projects_returns_empty(self, tmp_path):
        result = reflect_on_experiments(tmp_path / "nonexistent")
        assert result == {}

    def test_with_project_data(self, tmp_path):
        proj = tmp_path / "test-project"
        proj.mkdir()
        rows = [
            {
                "id": str(i),
                "hypothesis": "Add logging coverage",
                "verdict": "keep",
                "delta": "0.01",
                "timestamp": datetime.now().isoformat(),
                "change_summary": "added logging",
                "notes": "",
            }
            for i in range(6)
        ]
        _write_tsv(proj / ".factory" / "results.tsv", rows)

        result = reflect_on_experiments(tmp_path, project_path=None)
        assert len(result) > 0
        assert "strategist" in result


# ── Curator ────────────────────────────────────────────────────


class TestCurator:
    def test_merge_new_candidates(self):
        existing = Playbook.empty("strategist")
        candidates = [
            PlaybookItem(id="x", content="New rule", helpful=5, harmful=0, section="DO"),
        ]
        updated = curate_playbook(existing, candidates)
        assert len(updated.items) == 1
        assert updated.items[0].content == "New rule"

    def test_dedup_merges_counters(self):
        existing = Playbook(role="strategist", items=[
            PlaybookItem(id="strat-00001", content="Prioritize features over hygiene work", helpful=5, harmful=1, section="DO"),
        ])
        candidates = [
            PlaybookItem(id="x", content="Prioritize features over hygiene work always", helpful=3, harmful=0, section="DO"),
        ]
        updated = curate_playbook(existing, candidates)
        assert len(updated.items) == 1
        assert updated.items[0].helpful == 8  # 5 + 3
        assert updated.items[0].harmful == 1  # 1 + 0

    def test_removes_net_negative(self):
        existing = Playbook(role="strategist", items=[
            PlaybookItem(id="a", content="Good rule", helpful=5, harmful=1, section="DO"),
            PlaybookItem(id="b", content="Bad rule", helpful=1, harmful=5, section="DON'T"),
        ])
        updated = curate_playbook(existing, [])
        assert len(updated.items) == 1
        assert updated.items[0].content == "Good rule"

    def test_caps_at_max_items(self):
        # Use highly distinct content to avoid dedup merging
        topics = [
            "Prioritize features over hygiene when scores are high",
            "Always reference vault source notes in hypotheses",
            "Skip observability when coverage exceeds threshold",
            "Run Playwright MCP to verify UI changes before committing",
            "Ground new capabilities in research papers from arxiv",
            "Use FEEC priority ordering for all hypothesis ranking",
            "Detect stuck patterns after three consecutive reverts",
            "Balance experiment categories across design space dimensions",
            "Check cross-project insights before proposing new work",
            "Avoid proposing changes outside the declared modifiable scope",
            "Test with real databases not mocks for integration tests",
            "Keep change summaries concise and under fifty words maximum",
            "Dedup near-identical playbook bullets using sequence matching",
            "Cap maximum playbook size to prevent context window overflow",
            "Merge helpful and harmful counters when combining similar items",
            "Assign sequential IDs with role prefix after each curation pass",
            "Remove net-negative items only with sufficient observation count",
            "Sort items by net score descending for priority visibility",
            "Write structured logs with structlog not print statements",
            "Validate all pydantic models with strict mode and extra forbid",
        ]
        items = [
            PlaybookItem(
                id=f"strat-{i:05d}",
                content=topics[i],
                helpful=i + 1,
                harmful=0,
                section="DO",
            )
            for i in range(20)
        ]
        existing = Playbook(role="strategist", items=items)
        updated = curate_playbook(existing, [], max_items=5)
        assert len(updated.items) == 5
        # Highest net score items should be kept
        assert updated.items[0].helpful >= updated.items[-1].helpful

    def test_idempotent_on_clean_playbook(self):
        items = [
            PlaybookItem(id="strat-00001", content="Good rule", helpful=5, harmful=0, section="DO"),
        ]
        existing = Playbook(role="strategist", items=items)
        updated = curate_playbook(existing, [])
        assert len(updated.items) == 1
        assert updated.items[0].content == "Good rule"

    def test_reassigns_ids(self):
        candidates = [
            PlaybookItem(id="x", content="Rule Alpha about strategy", helpful=3, harmful=0, section="DO"),
            PlaybookItem(id="y", content="Rule Beta about building", helpful=1, harmful=0, section="DO"),
        ]
        updated = curate_playbook(Playbook.empty("strategist"), candidates)
        assert len(updated.items) == 2
        assert updated.items[0].id == "strat-00001"
        assert updated.items[1].id == "strat-00002"


# ── Injector ───────────────────────────────────────────────────


class TestInjector:
    def test_load_playbook_missing(self):
        result = load_playbook("nonexistent_role_xyz")
        assert result is None

    def test_inject_playbook(self):
        prompt = "You are the Strategist agent."
        playbook = "### DO\n- [strat-00001] helpful=5 harmful=0 :: Prioritize features"
        result = inject_playbook(prompt, playbook)
        assert "Behavioral Playbook" in result
        assert "Prioritize features" in result
        assert result.startswith("You are the Strategist agent.")


# ── CLI integration ────────────────────────────────────────────


class TestCmdAce:
    def test_dry_run(self, tmp_path):
        """factory ace --dry-run should not create playbook files."""
        from factory.cli import cmd_ace

        proj = tmp_path / "test-proj"
        proj.mkdir()
        rows = [
            {
                "id": str(i),
                "hypothesis": "Add logging",
                "verdict": "keep",
                "delta": "0.01",
                "timestamp": datetime.now().isoformat(),
                "change_summary": "added logging",
                "notes": "",
            }
            for i in range(6)
        ]
        _write_tsv(proj / ".factory" / "results.tsv", rows)

        class FakeArgs:
            path = str(proj)
            projects_dir = str(tmp_path)
            dry_run = True

        result = cmd_ace(FakeArgs())
        assert result == 0
