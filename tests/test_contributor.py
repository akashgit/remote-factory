"""Tests for factory/ace/contributor.py — classification, diffing, summary, PR body, persistence."""

import argparse
import subprocess
from datetime import datetime
from unittest.mock import MagicMock, patch

from factory.ace.contributor import (
    ClassifiedItem,
    ContributionReport,
    EvidencePackage,
    classify_evolved_playbooks,
    classify_item,
    diff_playbooks,
    execute_contribution,
    explain_specificity,
    explain_uncertainty,
    generate_pr_body,
    load_candidates,
    package_evidence,
    prepare_contribution,
    render_generality_bar,
    render_summary,
    save_candidates,
    score_category_signal,
    score_cross_project_prevalence,
    score_domain_independence,
    score_evidence_strength,
)
from factory.ace.models import Playbook, PlaybookItem
from factory.cli import cmd_contribute
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

    def test_load_candidates_missing_file(self, tmp_path):
        result = load_candidates(tmp_path / "nonexistent")
        assert result is None

    def test_load_candidates_corrupt_file(self, tmp_path):
        out = tmp_path / ".factory" / "contribution_candidates.json"
        out.parent.mkdir(parents=True)
        out.write_text("not valid json {{{")
        result = load_candidates(tmp_path)
        assert result is None


# ── classify_evolved_playbooks ─────────────────────────────────


class TestClassifyEvolvedPlaybooks:
    def test_full_pipeline(self, tmp_path):
        pb_content = (
            "---\nrole: builder\nupdated: 2026-01-01\nitem_count: 1\n---\n\n"
            "## Behavioral Playbook — Builder\n\n### DO\n"
            "- [bldr-001] helpful=5 harmful=0 :: Improve agent prompt eval scoring\n"
        )
        evolved_dir = tmp_path / "evolved"
        evolved_dir.mkdir()
        (evolved_dir / "builder.md").write_text(pb_content)

        defaults_dir = tmp_path / "defaults"
        defaults_dir.mkdir()

        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.ace.contributor.user_playbooks_dir", return_value=evolved_dir),
            patch("factory.ace.contributor.DEFAULTS_DIR", defaults_dir),
            patch("factory.ace.contributor.discover_projects", return_value=[]),
            patch("factory.ace.contributor.load_all_histories", return_value={}),
        ):
            report = classify_evolved_playbooks(project_path)

        assert isinstance(report, ContributionReport)
        total = len(report.general_items) + len(report.specific_items) + len(report.uncertain_items)
        assert total == 1
        all_items = report.general_items + report.specific_items + report.uncertain_items
        assert all_items[0].item.content == "Improve agent prompt eval scoring"
        assert all_items[0].role == "builder"

    def test_with_default_playbook(self, tmp_path):
        evolved_dir = tmp_path / "evolved"
        evolved_dir.mkdir()
        (evolved_dir / "strategist.md").write_text(
            "---\nrole: strategist\nupdated: 2026-01-01\nitem_count: 1\n---\n\n"
            "## Behavioral Playbook — Strategist\n\n### DO\n"
            "- [strat-001] helpful=8 harmful=0 :: Improve agent prompt clarity and structure\n"
        )

        defaults_dir = tmp_path / "defaults"
        defaults_dir.mkdir()
        (defaults_dir / "strategist.md").write_text(
            "---\nrole: strategist\nupdated: 2025-01-01\nitem_count: 1\n---\n\n"
            "## Behavioral Playbook — Strategist\n\n### DO\n"
            "- [strat-001] helpful=2 harmful=0 :: Improve agent prompt clarity and structure\n"
        )

        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.ace.contributor.user_playbooks_dir", return_value=evolved_dir),
            patch("factory.ace.contributor.DEFAULTS_DIR", defaults_dir),
            patch("factory.ace.contributor.discover_projects", return_value=[]),
            patch("factory.ace.contributor.load_all_histories", return_value={}),
        ):
            report = classify_evolved_playbooks(project_path)

        total = len(report.general_items) + len(report.specific_items) + len(report.uncertain_items)
        assert total == 1


# ── diff_playbooks: content-changed-but-matched branch ─────────


class TestDiffPlaybooksContentChanged:
    def test_content_different_but_fuzzy_matched(self):
        evolved = Playbook(role="test", items=[
            PlaybookItem(
                id="e-001",
                content="Always run unit tests and integration tests before committing",
                helpful=5,
                harmful=0,
            ),
        ])
        default = Playbook(role="test", items=[
            PlaybookItem(
                id="d-001",
                content="Always run unit tests before committing",
                helpful=5,
                harmful=0,
            ),
        ])
        diffs = diff_playbooks(evolved, default)
        assert len(diffs) == 1
        assert diffs[0]["diff_type"] == "modified"
        assert diffs[0]["evolved_item"].content == "Always run unit tests and integration tests before committing"
        assert diffs[0]["default_item"].content == "Always run unit tests before committing"


# ── package_evidence ───────────────────────────────────────────


class TestPackageEvidence:
    def test_with_matching_experiments(self):
        c = _classified("Add logging to all modules", helpful=5, source_projects=["a", "b"])
        data = {
            "proj-a": [
                _experiment("Add logging to all modules", verdict="keep", id=1),
                _experiment("Add logging to all modules", verdict="keep", id=2),
            ],
            "proj-b": [
                _experiment("Add logging to all modules", verdict="revert", id=3),
            ],
            "proj-c": [
                _experiment("Unrelated hypothesis", verdict="keep", id=4),
            ],
        }
        ev = package_evidence(c, data)
        assert isinstance(ev, EvidencePackage)
        assert ev.total_projects == 2
        assert ev.total_experiments == 3
        assert "proj-a" in ev.cross_project_stats
        assert "proj-b" in ev.cross_project_stats
        assert "proj-c" not in ev.cross_project_stats
        assert ev.cross_project_stats["proj-a"] == 1.0
        assert ev.cross_project_stats["proj-b"] == 0.0
        assert len(ev.example_experiments) == 3
        assert ev.confidence == 0.3
        assert ev.category != ""

    def test_examples_capped_at_five(self):
        c = _classified("Common rule across many projects", helpful=10)
        data = {}
        for i in range(8):
            data[f"proj-{i}"] = [
                _experiment("Common rule across many projects", verdict="keep", id=i),
            ]
        ev = package_evidence(c, data)
        assert len(ev.example_experiments) == 5

    def test_no_matches(self):
        c = _classified("Unique rule", helpful=1)
        data = {
            "proj-a": [_experiment("Totally different hypothesis")],
        }
        ev = package_evidence(c, data)
        assert ev.total_projects == 0
        assert ev.total_experiments == 0
        assert ev.example_experiments == []


# ── generate_pr_body with evidence ─────────────────────────────


class TestGeneratePrBodyWithEvidence:
    def test_evidence_includes_keep_rate(self):
        candidates = [
            _classified("Improve prompt clarity", helpful=8, role="strategist", source_projects=["a", "b"]),
        ]
        candidates[0].generality_score = 0.8
        evidence_map = {
            candidates[0].item.id: EvidencePackage(
                cross_project_stats={"proj-a": 0.9, "proj-b": 0.8},
                total_experiments=10,
                total_projects=2,
                example_experiments=["proj-a: Improve prompt clarity"],
                category="prompt_engineering",
                confidence=0.8,
            ),
        }
        body = generate_pr_body(candidates, evidence_map)
        assert "keep rate" in body
        assert "2 projects" in body
        assert "10 experiments" in body
        assert "85%" in body


# ── prepare_contribution ───────────────────────────────────────


class TestPrepareContribution:
    def test_basic_contribution(self, tmp_path):
        factory_repo = tmp_path / "factory-repo"
        playbooks_dir = factory_repo / "factory" / "agents" / "playbooks"
        playbooks_dir.mkdir(parents=True)

        (playbooks_dir / "strategist.md").write_text(
            "---\nrole: strategist\nupdated: 2025-01-01\nitem_count: 1\n---\n\n"
            "## Behavioral Playbook — Strategist\n\n### DO\n"
            "- [strat-001] helpful=2 harmful=0 :: Existing rule about prompts\n"
        )

        candidates = [
            _classified("Brand new rule about testing", helpful=10, role="strategist", source_projects=["a", "b", "c"]),
        ]
        candidates[0].generality_score = 0.8

        result = prepare_contribution(candidates, factory_repo)

        assert "branch_name" in result
        assert result["branch_name"].startswith("meta-contrib/")
        assert len(result["file_changes"]) == 1
        assert result["file_changes"][0]["path"] == "factory/agents/playbooks/strategist.md"
        assert "Brand new rule about testing" in result["file_changes"][0]["content"]
        assert "Existing rule about prompts" in result["file_changes"][0]["content"]
        assert "commit_message" in result
        assert "pr_title" in result
        assert "pr_body" in result
        assert "Meta Mode Contribution" in result["pr_body"]

    def test_fuzzy_merge_updates_existing(self, tmp_path):
        factory_repo = tmp_path / "factory-repo"
        playbooks_dir = factory_repo / "factory" / "agents" / "playbooks"
        playbooks_dir.mkdir(parents=True)

        (playbooks_dir / "builder.md").write_text(
            "---\nrole: builder\nupdated: 2025-01-01\nitem_count: 1\n---\n\n"
            "## Behavioral Playbook — Builder\n\n### DO\n"
            "- [bldr-001] helpful=2 harmful=0 :: Always run tests before committing\n"
        )

        candidates = [
            _classified(
                "Always run tests before committing changes to the repo",
                helpful=10, role="builder", source_projects=["a", "b"],
            ),
        ]
        candidates[0].generality_score = 0.75

        result = prepare_contribution(candidates, factory_repo)

        content = result["file_changes"][0]["content"]
        assert "Always run tests before committing changes to the repo" in content
        lines = [line for line in content.splitlines() if line.startswith("- [")]
        assert len(lines) == 1

    def test_no_existing_default(self, tmp_path):
        factory_repo = tmp_path / "factory-repo"
        playbooks_dir = factory_repo / "factory" / "agents" / "playbooks"
        playbooks_dir.mkdir(parents=True)

        candidates = [
            _classified("New rule", helpful=5, role="reviewer", source_projects=["a"]),
        ]
        candidates[0].generality_score = 0.7

        result = prepare_contribution(candidates, factory_repo)

        assert result["file_changes"][0]["path"] == "factory/agents/playbooks/reviewer.md"
        assert "New rule" in result["file_changes"][0]["content"]

    def test_with_cross_project_data(self, tmp_path):
        factory_repo = tmp_path / "factory-repo"
        playbooks_dir = factory_repo / "factory" / "agents" / "playbooks"
        playbooks_dir.mkdir(parents=True)

        candidates = [
            _classified("Improve logging", helpful=8, role="builder", source_projects=["a", "b"]),
        ]
        candidates[0].generality_score = 0.8

        cross_project_data = {
            "proj-a": [_experiment("Improve logging", verdict="keep")],
            "proj-b": [_experiment("Improve logging", verdict="keep")],
        }

        result = prepare_contribution(candidates, factory_repo, cross_project_data)

        assert "keep rate" in result["pr_body"]


# ── explain_specificity branches ───────────────────────────────


class TestExplainSpecificityBranches:
    def test_domain_keywords(self):
        c = _classified("Use Django REST framework for API endpoints", helpful=5, source_projects=["a", "b", "c"])
        reason = explain_specificity(c)
        assert "domain-specific" in reason
        assert "Django" in reason

    def test_low_evidence(self):
        c = _classified("Some rule with little data", helpful=1, harmful=0, source_projects=["a", "b", "c"])
        reason = explain_specificity(c)
        assert "low evidence" in reason

    def test_high_variance(self):
        c = _classified("Rule that varies across projects", helpful=5, harmful=3, source_projects=["a", "b"])
        reason = explain_specificity(c)
        assert "high variance" in reason

    def test_low_consensus(self):
        c = _classified("Divisive rule", helpful=2, harmful=3, source_projects=["a", "b", "c"])
        reason = explain_specificity(c)
        assert "low consensus" in reason

    def test_fallback(self):
        c = _classified(
            "Improve agent prompt eval scoring for experiments",
            helpful=10, harmful=0, source_projects=["a", "b", "c"],
        )
        reason = explain_specificity(c)
        assert "below generality threshold" in reason


# ── explain_uncertainty branches ───────────────────────────────


class TestExplainUncertaintyBranches:
    def test_mixed_signals(self):
        c = _classified(helpful=8, harmful=4, source_projects=["a", "b", "c"])
        reason = explain_uncertainty(c)
        assert "mixed signals" in reason

    def test_insufficient_observations(self):
        c = _classified(helpful=3, harmful=0, source_projects=["a", "b", "c"])
        reason = explain_uncertainty(c)
        assert "insufficient observations" in reason


# ── execute_contribution ───────────────────────────────────────


class TestExecuteContribution:
    def _make_spec(self):
        return {
            "branch_name": "meta-contrib/2026-05-25-playbook-updates",
            "file_changes": [
                {"path": "factory/agents/playbooks/builder.md", "content": "updated content"},
            ],
            "commit_message": "Update playbook items",
            "pr_title": "Meta mode: update 1 playbook item",
            "pr_body": "## Meta Mode Contribution\n\nTest body",
        }

    @patch("factory.ace.contributor.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        pr_url = "https://github.com/org/repo/pull/42"
        mock_run.return_value = MagicMock(stdout=pr_url, returncode=0)

        result = execute_contribution(self._make_spec(), tmp_path)

        assert result == pr_url
        assert mock_run.call_count == 5

        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["git", "checkout", "-b", "meta-contrib/2026-05-25-playbook-updates"]
        assert calls[1][0][0] == ["git", "add", "factory/agents/playbooks/builder.md"]
        assert calls[2][0][0][0:2] == ["git", "commit"]
        assert calls[3][0][0][0:2] == ["git", "push"]
        assert calls[4][0][0][0:2] == ["gh", "pr"]

    @patch("factory.ace.contributor.subprocess.run")
    def test_error_cleans_up_branch(self, mock_run, tmp_path):
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "git", stderr="branch already exists"),
        ]

        call_count = 0

        def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise subprocess.CalledProcessError(1, "git", stderr="branch already exists")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect_fn

        result = execute_contribution(self._make_spec(), tmp_path)

        assert result.startswith("Error:")
        assert "branch already exists" in result
        assert mock_run.call_count == 3


# ── cmd_contribute CLI ─────────────────────────────────────────


class TestCmdContribute:
    def _make_args(self, path="/tmp/test-project", submit=False, status=False):
        args = argparse.Namespace()
        args.path = path
        args.submit = submit
        args.status = status
        args.projects_dir = None
        args.dry_run = False
        return args

    @patch("factory.cli._emit_cli_event")
    @patch("factory.cli.cmd_contribute.__module__", "factory.cli")
    @patch("factory.ace.contributor.save_candidates")
    @patch("factory.ace.contributor.render_summary", return_value="SUMMARY OUTPUT")
    @patch("factory.ace.contributor.classify_evolved_playbooks")
    def test_classify(self, mock_classify, mock_render, mock_save, mock_emit):
        report = ContributionReport(
            general_items=[_classified("General", helpful=10, source_projects=["a", "b"])],
            specific_items=[],
            uncertain_items=[],
            generated_at="2026-01-01T00:00:00Z",
        )
        mock_classify.return_value = report

        args = self._make_args()
        with patch("factory.registry.get_project_paths", return_value=[]):
            ret = cmd_contribute(args)

        assert ret == 0
        mock_classify.assert_called_once()
        mock_render.assert_called_once_with(report)
        mock_save.assert_called_once()

    @patch("factory.ace.contributor.load_candidates", return_value=None)
    def test_status_no_candidates(self, mock_load, capsys):
        args = self._make_args(status=True)
        ret = cmd_contribute(args)
        assert ret == 0
        captured = capsys.readouterr()
        assert "No pending contributions" in captured.out

    @patch("factory.ace.contributor.load_candidates")
    def test_status_with_candidates(self, mock_load, capsys):
        report = ContributionReport(
            general_items=[_classified("G1", helpful=5)],
            specific_items=[_classified("S1", helpful=2), _classified("S2", helpful=1)],
            uncertain_items=[],
            generated_at="2026-01-01T00:00:00Z",
        )
        mock_load.return_value = report

        args = self._make_args(status=True)
        ret = cmd_contribute(args)
        assert ret == 0
        captured = capsys.readouterr()
        assert "1 general" in captured.out
        assert "2 specific" in captured.out

    @patch("factory.ace.contributor.load_candidates", return_value=None)
    def test_submit_no_candidates(self, mock_load, capsys):
        args = self._make_args(submit=True)
        ret = cmd_contribute(args)
        assert ret == 1
        captured = capsys.readouterr()
        assert "No contribution candidates found" in captured.out
