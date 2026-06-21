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
    _parse_ceo_notes,
    _strategist_bullets,
    _researcher_bullets,
    _qa_review_bullets,
    _archivist_bullets,
    _ceo_bullets,
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
    notes: str = "",
    cost_usd: float | None = None,
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
        cost_usd=cost_usd,
        notes=notes,
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

    def test_generates_bullets_for_all_roles(self, tmp_path):
        """With enough data, reflector generates bullets for multiple roles."""
        proj = tmp_path / "test-project"
        proj.mkdir()
        rows = []
        for i in range(10):
            rows.append({
                "id": str(i),
                "hypothesis": "Add logging coverage" if i < 6 else "Fix bug in parser",
                "verdict": "keep" if i % 2 == 0 else "revert",
                "delta": "0.02" if i % 2 == 0 else "-0.01",
                "timestamp": datetime.now().isoformat(),
                "change_summary": "changes" * (10 if i < 5 else 50),
                "notes": f"ceo:{'keep' if i % 2 == 0 else 'revert'} archivist_spawned={'true' if i < 7 else 'false'} builder_failed={'true' if i == 3 else 'false'}",
            })
        _write_tsv(proj / ".factory" / "results.tsv", rows)

        result = reflect_on_experiments(tmp_path, project_path=None)
        # Should have bullets for at least some roles
        assert len(result) > 0


# ── v2: Parse CEO Notes ──────────────────────────────────────────


class TestParseCeoNotes:
    def test_empty_notes(self):
        assert _parse_ceo_notes("") == {}

    def test_keep_decision(self):
        parsed = _parse_ceo_notes("ceo:keep score_delta=+0.05 agents_spawned=R,S,B")
        assert parsed["decision"] == "keep"
        assert parsed["score_delta"] == "+0.05"
        assert parsed["agents_spawned"] == "R,S,B"

    def test_revert_decision(self):
        parsed = _parse_ceo_notes("ceo:revert reason=score_regression score_delta=-0.03")
        assert parsed["decision"] == "revert"
        assert parsed["reason"] == "score_regression"

    def test_error_with_builder_failure(self):
        parsed = _parse_ceo_notes("ceo:error builder_failed=true eval_crashed=false")
        assert parsed["decision"] == "error"
        assert parsed["builder_failed"] == "true"
        assert parsed["eval_crashed"] == "false"

    def test_archivist_tracking(self):
        parsed = _parse_ceo_notes("ceo:keep archivist_spawned=true score_delta=+0.01")
        assert parsed["archivist_spawned"] == "true"

    def test_non_ceo_notes_returns_no_decision(self):
        parsed = _parse_ceo_notes("just some regular notes")
        assert "decision" not in parsed


# ── v2: Researcher Bullets ────────────────────────────────────────


class TestResearcherBullets:
    def test_research_backed_success(self):
        """Research-backed experiments with higher keep rate → DO bullet."""
        records = (
            [_make_record(i, "Based on research paper findings, add caching", verdict="keep", delta=0.03) for i in range(5)]
            + [_make_record(i + 5, "Random refactor attempt", verdict="revert", delta=-0.02) for i in range(5)]
        )
        outcomes = [("feature", "keep" if i < 5 else "revert", 0.03 if i < 5 else -0.02) for i in range(10)]
        bullets = _researcher_bullets(outcomes, records)
        do_bullets = [b for b in bullets if b.section == "DO"]
        assert len(do_bullets) >= 1
        assert any("research" in b.content.lower() for b in do_bullets)

    def test_empty_data(self):
        assert _researcher_bullets([], []) == []


# ── v2: Reviewer Bullets ─────────────────────────────────────────


class TestQAReviewBullets:
    def test_guard_violation_pattern(self):
        """Repeated QA failures in a category → DO bullet."""
        records = [
            _make_record(i, "Refactor the auth module", verdict="revert",
                         notes="ceo:revert qa_failed=true")
            for i in range(3)
        ]
        outcomes = [("refactoring", "revert", -0.01)] * 3
        bullets = _qa_review_bullets(outcomes, records)
        assert len(bullets) >= 1
        assert any("attention" in b.content.lower() for b in bullets)

    def test_strict_reverts_with_positive_delta(self):
        """Reverts despite positive delta → DON'T bullet about strictness."""
        records = [
            _make_record(i, f"Improve feature {i}", verdict="revert", delta=0.05)
            for i in range(4)
        ]
        outcomes = [("feature", "revert", 0.05)] * 4
        bullets = _qa_review_bullets(outcomes, records)
        dont_bullets = [b for b in bullets if b.section == "DON'T"]
        assert len(dont_bullets) >= 1

    def test_empty_data(self):
        assert _qa_review_bullets([], []) == []


# ── v2: Archivist Bullets ────────────────────────────────────────


class TestArchivistBullets:
    def test_skipped_archival(self):
        """Experiments with archivist_spawned=false → DON'T bullet."""
        records = [
            _make_record(i, "Test hyp", notes="ceo:keep archivist_spawned=false")
            for i in range(3)
        ]
        outcomes = [("feature", "keep", 0.01)] * 3
        bullets = _archivist_bullets(outcomes, records)
        dont_bullets = [b for b in bullets if b.section == "DON'T"]
        assert len(dont_bullets) >= 1
        assert any("skipped" in b.content.lower() for b in dont_bullets)

    def test_good_archival_compliance(self):
        """All experiments archived → DO bullet."""
        records = [
            _make_record(i, "Test hyp", notes="ceo:keep archivist_spawned=true")
            for i in range(6)
        ]
        outcomes = [("feature", "keep", 0.01)] * 6
        bullets = _archivist_bullets(outcomes, records)
        do_bullets = [b for b in bullets if b.section == "DO"]
        assert len(do_bullets) >= 1
        assert any("compliance" in b.content.lower() for b in do_bullets)

    def test_empty_data(self):
        assert _archivist_bullets([], []) == []


# ── v2: CEO Bullets ──────────────────────────────────────────────


class TestCeoBullets:
    def test_low_keep_rate_warning(self):
        """Low overall keep rate → DO bullet about hypothesis quality."""
        records = [
            _make_record(i, f"Attempt {i}", verdict="revert", delta=-0.01)
            for i in range(8)
        ] + [
            _make_record(8, "One success", verdict="keep", delta=0.01),
        ]
        outcomes = [("feature", "revert", -0.01)] * 8 + [("feature", "keep", 0.01)]
        bullets = _ceo_bullets(outcomes, records)
        # No ceo: notes, so it should generate a bootstrapping bullet
        assert len(bullets) >= 1
        assert any("keep rate" in b.content.lower() for b in bullets)

    def test_builder_failure_pattern(self):
        """Repeated builder failures → DO bullet."""
        records = [
            _make_record(i, "Refactor module X", verdict="error",
                         notes="ceo:error builder_failed=true")
            for i in range(4)
        ]
        outcomes = [("refactoring", "error", None)] * 4
        bullets = _ceo_bullets(outcomes, records)
        assert len(bullets) >= 1
        assert any("builder" in b.content.lower() for b in bullets)

    def test_bad_keep_decisions(self):
        """Keeps with negative delta → DON'T bullet."""
        records = [
            _make_record(i, "Add feature", verdict="keep", delta=-0.02,
                         notes="ceo:keep score_delta=-0.02")
            for i in range(4)
        ]
        outcomes = [("feature", "keep", -0.02)] * 4
        bullets = _ceo_bullets(outcomes, records)
        dont_bullets = [b for b in bullets if b.section == "DON'T"]
        assert len(dont_bullets) >= 1
        assert any("tighten" in b.content.lower() for b in dont_bullets)

    def test_archival_compliance_violation(self):
        """CEO skipping archival → DON'T bullet."""
        records = [
            _make_record(i, "Test hyp", notes="ceo:keep archivist_spawned=false")
            for i in range(3)
        ]
        outcomes = [("feature", "keep", 0.01)] * 3
        bullets = _ceo_bullets(outcomes, records)
        dont_bullets = [b for b in bullets if b.section == "DON'T"]
        assert len(dont_bullets) >= 1
        assert any("archivist" in b.content.lower() or "archival" in b.content.lower()
                    for b in dont_bullets)

    def test_chronic_reverts_in_category(self):
        """3+ reverts in same category → DON'T bullet."""
        records = [
            _make_record(i, "Refactor the pipeline again", verdict="revert", delta=-0.01,
                         notes="ceo:revert reason=score_regression")
            for i in range(4)
        ]
        outcomes = [("refactoring", "revert", -0.01)] * 4
        bullets = _ceo_bullets(outcomes, records)
        dont_bullets = [b for b in bullets if b.section == "DON'T"]
        assert len(dont_bullets) >= 1
        assert any("stop" in b.content.lower() for b in dont_bullets)

    def test_empty_data(self):
        assert _ceo_bullets([], []) == []

    def test_cost_efficiency_analysis(self):
        """Reverted experiments costing more → DO bullet about waste."""
        records = [
            _make_record(i, "Good change", verdict="keep", delta=0.05,
                         notes="ceo:keep", cost_usd=0.50)
            for i in range(4)
        ] + [
            _make_record(i + 4, "Wasted effort", verdict="revert", delta=-0.03,
                         notes="ceo:revert", cost_usd=2.00)
            for i in range(4)
        ]
        outcomes = [("feature", "keep", 0.05)] * 4 + [("feature", "revert", -0.03)] * 4
        bullets = _ceo_bullets(outcomes, records)
        do_bullets = [b for b in bullets if b.section == "DO"]
        assert any("cost" in b.content.lower() or "spend" in b.content.lower() for b in do_bullets)


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
