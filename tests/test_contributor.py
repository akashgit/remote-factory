"""Tests for factory/ace/contributor.py — classification, diffing, summary, PR body, persistence."""

from datetime import datetime

import pytest

from factory.ace.contributor import (
    ClassifiedItem,
    ContributionReport,
    classify_item,
    diff_playbooks,
    explain_specificity,
    explain_uncertainty,
    generate_pr_body,
    load_candidates,
    render_generality_bar,
    render_summary,
    save_candidates,
    score_category_signal,
    score_cross_project_prevalence,
    score_domain_independence,
    score_evidence_strength,
)
from factory.ace.models import Playbook, PlaybookItem
from factory.models import ExperimentRecord


# ── helpers ─────────────────────────────────────────────────────


def _item(content: str, helpful: int = 0, harmful: int = 0, section: str = "DO") -> PlaybookItem:
    return PlaybookItem(id="test-001", content=content, helpful=helpful, harmful=harmful, section=section)


def _classified(
    content: str = "Test rule",
    helpful: int = 0,
    harmful: int = 0,
    role: str = "strategist",
    source_projects: list[str] | None = None,
) -> ClassifiedItem:
    item = _item(content, helpful=helpful, harmful=harmful)
    return ClassifiedItem(
        item=item,
        role=role,
        source_projects=source_projects or [],
    )


def _experiment(
    hypothesis: str,
    verdict: str = "keep",
    id: int = 1,
) -> ExperimentRecord:
    return ExperimentRecord(
        id=id,
        timestamp=datetime.now(),
        hypothesis=hypothesis,
        change_summary="test change",
        issue_number=None,
        pr_number=None,
        score_before=None,
        score_after=None,
        delta=None,
        verdict=verdict,
        cost_usd=None,
        notes="",
    )


# ── Classification: cross-project prevalence ────────────────────


class TestScoreCrossProjectPrevalence:
    def test_no_match(self):
        c = _classified("Completely unrelated rule")
        score = score_cross_project_prevalence(c, {})
        assert score == 0.0

    def test_one_project(self):
        c = _classified("Add logging to all modules")
        data = {
            "proj-a": [_experiment("Add logging to all modules")],
        }
        score = score_cross_project_prevalence(c, data)
        assert abs(score - 1 / 3) < 0.15

    def test_three_projects(self):
        c = _classified("Add logging to all modules")
        data = {
            "proj-a": [_experiment("Add logging to all modules")],
            "proj-b": [_experiment("Add logging to all modules")],
            "proj-c": [_experiment("Add logging to all modules")],
        }
        score = score_cross_project_prevalence(c, data)
        assert score == 1.0


# ── Classification: domain independence ─────────────────────────


class TestScoreDomainIndependence:
    def test_factory_keywords(self):
        c = _classified("Improve agent prompt eval scoring for experiments")
        score = score_domain_independence(c)
        assert score > 0.7

    def test_domain_keywords(self):
        c = _classified("Use React hooks with Django REST framework and PostgreSQL")
        score = score_domain_independence(c)
        assert score < 0.3

    def test_neutral(self):
        c = _classified("Keep variables short and descriptive")
        score = score_domain_independence(c)
        assert score == 0.5


# ── Classification: evidence strength ───────────────────────────


class TestScoreEvidenceStrength:
    def test_low(self):
        c = _classified(helpful=1, harmful=0)
        score = score_evidence_strength(c)
        assert score == 0.2

    def test_high(self):
        c = _classified(helpful=12, harmful=1)
        score = score_evidence_strength(c)
        assert score > 0.5


# ── Classification: category signal ─────────────────────────────


class TestScoreCategorySignal:
    def test_general(self):
        c = _classified("Improve agent prompt clarity and structure")
        score = score_category_signal(c)
        assert score == 0.9

    def test_specific(self):
        c = _classified("Add user dashboard feature with sidebar navigation")
        score = score_category_signal(c)
        assert score == 0.3


# ── Classification: end-to-end ──────────────────────────────────


class TestClassifyItem:
    def test_general(self):
        item = _item("Improve agent prompt eval scoring", helpful=10, harmful=0)
        data = {
            f"proj-{i}": [_experiment("Improve agent prompt eval scoring")]
            for i in range(3)
        }
        result = classify_item(item, data, role="strategist")
        assert result.classification == "general"

    def test_specific(self):
        item = _item("Add React component for Django admin", helpful=1, harmful=0)
        result = classify_item(item, {}, role="builder")
        assert result.classification == "specific"

    def test_uncertain(self):
        item = _item("Improve test coverage for modules", helpful=4, harmful=2)
        data = {
            "proj-a": [_experiment("Improve test coverage for modules")],
        }
        result = classify_item(item, data, role="strategist")
        assert result.classification == "uncertain"


# ── Diff ────────────────────────────────────────────────────────


class TestDiffPlaybooks:
    def test_added(self):
        evolved = Playbook(role="test", items=[
            _item("Brand new rule", helpful=3),
        ])
        default = Playbook.empty("test")
        diffs = diff_playbooks(evolved, default)
        assert len(diffs) == 1
        assert diffs[0]["diff_type"] == "added"
        assert diffs[0]["evolved_item"].content == "Brand new rule"
        assert "+[test-001]" in diffs[0]["diff_text"] or "Brand new rule" in diffs[0]["diff_text"]

    def test_modified(self):
        evolved = Playbook(role="test", items=[
            _item("Updated version of rule", helpful=5),
        ])
        default = Playbook(role="test", items=[
            _item("Updated version of rule", helpful=2),
        ])
        diffs = diff_playbooks(evolved, default)
        assert len(diffs) == 1
        assert diffs[0]["diff_type"] == "modified"

    def test_removed(self):
        evolved = Playbook.empty("test")
        default = Playbook(role="test", items=[
            _item("Old rule that was removed"),
        ])
        diffs = diff_playbooks(evolved, default)
        assert len(diffs) == 1
        assert diffs[0]["diff_type"] == "removed"
        assert diffs[0]["default_item"].content == "Old rule that was removed"
        assert diffs[0]["evolved_item"] is None

    def test_fuzzy_matching(self):
        evolved = Playbook(role="test", items=[
            PlaybookItem(id="e-001", content="Always run tests before committing changes", helpful=5, harmful=0),
        ])
        default = Playbook(role="test", items=[
            PlaybookItem(id="d-001", content="Always run tests before committing", helpful=2, harmful=0),
        ])
        diffs = diff_playbooks(evolved, default)
        assert len(diffs) == 1
        assert diffs[0]["diff_type"] == "modified"


# ── Summary: render_generality_bar ──────────────────────────────


class TestRenderGeneralityBar:
    def test_zero(self):
        bar = render_generality_bar(0.0)
        assert bar.startswith("░" * 10)
        assert "0.00" in bar

    def test_half(self):
        bar = render_generality_bar(0.5)
        assert "█████" in bar
        assert "0.50" in bar

    def test_full(self):
        bar = render_generality_bar(1.0)
        assert bar.startswith("█" * 10)
        assert "1.00" in bar


# ── Summary: explain_specificity ────────────────────────────────


class TestExplainSpecificity:
    def test_single_project(self):
        c = _classified(helpful=5, harmful=0, source_projects=["proj-a"])
        reason = explain_specificity(c)
        assert "single-project signal" in reason


# ── Summary: explain_uncertainty ────────────────────────────────


class TestExplainUncertainty:
    def test_low_projects(self):
        c = _classified(helpful=5, harmful=0, source_projects=["proj-a"])
        reason = explain_uncertainty(c)
        assert "threshold" in reason


# ── Summary: render_summary ─────────────────────────────────────


class TestRenderSummary:
    def test_all_sections(self):
        report = ContributionReport(
            general_items=[_classified("General rule", helpful=10, source_projects=["a", "b", "c"])],
            specific_items=[_classified("Specific rule", helpful=2, source_projects=["a"])],
            uncertain_items=[_classified("Uncertain rule", helpful=4, source_projects=["a", "b"])],
            generated_at="2026-01-01T00:00:00Z",
        )
        text = render_summary(report)
        assert "META MODE SUMMARY" in text
        assert "factory contribute" in text
        assert "GENERAL IMPROVEMENTS" in text
        assert "PROJECT-SPECIFIC" in text
        assert "UNCERTAIN" in text


# ── PR body ─────────────────────────────────────────────────────


class TestGeneratePrBody:
    def test_empty(self):
        body = generate_pr_body([])
        assert "No items" in body

    def test_with_candidates(self):
        candidates = [
            _classified("Improve prompt clarity", helpful=8, role="strategist", source_projects=["a", "b"]),
        ]
        candidates[0].generality_score = 0.8
        body = generate_pr_body(candidates)
        assert "Methodology" in body
        assert "Improve prompt clarity" in body
        assert "Meta Mode Contribution" in body


# ── Persistence ─────────────────────────────────────────────────


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        report = ContributionReport(
            general_items=[_classified("General rule", helpful=10, source_projects=["a", "b", "c"])],
            specific_items=[_classified("Specific rule", helpful=2, source_projects=["a"])],
            uncertain_items=[],
            generated_at="2026-01-01T00:00:00Z",
        )
        save_candidates(report, tmp_path)
        loaded = load_candidates(tmp_path)
        assert loaded is not None
        assert len(loaded.general_items) == 1
        assert len(loaded.specific_items) == 1
        assert loaded.general_items[0].item.content == "General rule"
        assert loaded.specific_items[0].item.content == "Specific rule"
        assert loaded.generated_at == report.generated_at
