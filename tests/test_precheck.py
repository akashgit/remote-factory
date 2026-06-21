"""Tests for the precheck gate, review system, and strategy similarity functions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.strategy import (
    _tokenize,
    find_anti_patterns,
    hypothesis_similarity,
)
from factory.precheck import (
    check_anti_pattern,
    check_score_direction,
    check_scope,
    check_surfaces,
    run_precheck,
)
from factory.review import (
    ReviewPayload,
    format_review,
    post_review,
)


# ── strategy: tokenize ────────────────────────────────────────


class TestTokenize:
    def test_basic(self):
        assert _tokenize("add new feature") == {"add", "new", "feature"}

    def test_short_words_filtered(self):
        tokens = _tokenize("a to it add fix")
        assert "a" not in tokens
        assert "to" not in tokens
        assert "it" not in tokens
        assert "add" in tokens
        assert "fix" in tokens

    def test_case_insensitive(self):
        assert _tokenize("Add NEW Feature") == {"add", "new", "feature"}

    def test_empty(self):
        assert _tokenize("") == set()


# ── strategy: hypothesis_similarity ───────────────────────────


class TestHypothesisSimilarity:
    def test_identical(self):
        assert hypothesis_similarity("add logging to the agent", "add logging to the agent") == 1.0

    def test_completely_different(self):
        s = hypothesis_similarity("add logging to the agent", "refactor database schema")
        assert s < 0.2

    def test_partial_overlap(self):
        s = hypothesis_similarity(
            "add structured logging to the eval runner",
            "add structured logging to the builder agent",
        )
        assert 0.4 < s < 0.9

    def test_empty_strings(self):
        assert hypothesis_similarity("", "anything here") == 0.0
        assert hypothesis_similarity("something", "") == 0.0
        assert hypothesis_similarity("", "") == 0.0

    def test_symmetric(self):
        a, b = "improve test coverage for eval", "add test coverage for models"
        assert hypothesis_similarity(a, b) == hypothesis_similarity(b, a)


# ── strategy: find_anti_patterns ──────────────────────────────


class TestFindAntiPatterns:
    def test_no_reverts(self):
        history = [
            {"id": 1, "hypothesis": "add logging", "verdict": "keep"},
        ]
        assert find_anti_patterns("add logging", history) == []

    def test_finds_similar_revert(self):
        history = [
            {"id": 1, "hypothesis": "add structured logging to the eval runner", "verdict": "revert"},
        ]
        matches = find_anti_patterns("add structured logging to the builder", history, similarity_threshold=0.4)
        assert len(matches) == 1
        assert matches[0]["id"] == 1
        assert "similarity" in matches[0]

    def test_skips_below_threshold(self):
        history = [
            {"id": 1, "hypothesis": "refactor database schema", "verdict": "revert"},
        ]
        matches = find_anti_patterns("add logging to agent", history, similarity_threshold=0.6)
        assert matches == []

    def test_skips_keeps(self):
        history = [
            {"id": 1, "hypothesis": "add logging to agent", "verdict": "keep"},
            {"id": 2, "hypothesis": "add logging to agent", "verdict": "error"},
        ]
        matches = find_anti_patterns("add logging to agent", history, similarity_threshold=0.5)
        assert matches == []

    def test_empty_history(self):
        assert find_anti_patterns("anything", []) == []


# ── precheck: check_score_direction ───────────────────────────


class TestCheckScoreDirection:
    def test_improvement(self):
        r = check_score_direction(0.7, 0.85, 0.8)
        assert r.passed
        assert r.name == "score_direction"

    def test_regression(self):
        r = check_score_direction(0.85, 0.7, 0.8)
        assert not r.passed
        assert "regressed" in r.detail.lower()

    def test_below_threshold(self):
        r = check_score_direction(0.6, 0.7, 0.8)
        assert not r.passed
        assert "threshold" in r.detail.lower()

    def test_equal_above_threshold(self):
        r = check_score_direction(0.85, 0.85, 0.8)
        assert r.passed

    def test_none_scores(self):
        r = check_score_direction(None, 0.85, 0.8)
        assert not r.passed
        assert "none" in r.detail.lower()

        r = check_score_direction(0.85, None, 0.8)
        assert not r.passed


# ── precheck: check_scope ─────────────────────────────────────


class TestCheckScope:
    @patch("factory.precheck.subprocess.run")
    def test_clean(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="clean\n", stderr="")
        r = check_scope(Path("/tmp/test"), "abc123")
        assert r.passed
        assert r.name == "scope"

    @patch("factory.precheck.subprocess.run")
    def test_violations(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="VIOLATION: modified eval/score.py\nVIOLATION: out of scope",
            stderr="",
        )
        r = check_scope(Path("/tmp/test"), "abc123")
        assert not r.passed
        assert "modified eval/score.py" in r.detail

    @patch("factory.precheck.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="guard", timeout=60)
        r = check_scope(Path("/tmp/test"), "abc123")
        assert not r.passed
        assert "timed out" in r.detail.lower()

    @patch("factory.precheck.subprocess.run")
    def test_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        r = check_scope(Path("/tmp/test"), "abc123")
        assert not r.passed
        assert "not found" in r.detail.lower()


# ── precheck: check_anti_pattern ──────────────────────────────


class TestCheckAntiPattern:
    def test_no_match(self):
        history = [{"id": 1, "hypothesis": "unrelated thing", "verdict": "revert"}]
        r = check_anti_pattern("add new feature", history)
        assert r.passed

    def test_match_found(self):
        history = [
            {"id": 5, "hypothesis": "add structured logging to eval runner", "verdict": "revert"},
        ]
        r = check_anti_pattern("add structured logging to the eval runner", history, similarity_threshold=0.5)
        assert not r.passed
        assert "#5" in r.detail

    def test_empty_history(self):
        r = check_anti_pattern("anything", [])
        assert r.passed


# ── precheck: check_surfaces ─────────────────────────────────


class TestCheckSurfaces:
    @patch("factory.precheck.subprocess.run")
    def test_clean(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="clean\n", stderr="")
        r = check_surfaces(Path("/tmp/test"), "abc123")
        assert r.passed
        assert r.name == "fixed_surfaces"

    @patch("factory.precheck.subprocess.run")
    def test_violations(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="VIOLATION: Fixed surface modified: data/truth.json",
            stderr="",
        )
        r = check_surfaces(Path("/tmp/test"), "abc123")
        assert not r.passed
        assert "truth.json" in r.detail

    @patch("factory.precheck.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="guard", timeout=60)
        r = check_surfaces(Path("/tmp/test"), "abc123")
        assert not r.passed
        assert "timed out" in r.detail.lower()

    @patch("factory.precheck.subprocess.run")
    def test_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        r = check_surfaces(Path("/tmp/test"), "abc123")
        assert not r.passed
        assert "not found" in r.detail.lower()


# ── precheck: run_precheck ────────────────────────────────────


class TestRunPrecheck:
    def test_all_pass(self):
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="add new feature",
            history=[],
            project_path=Path("/tmp"),
        )
        assert result.passed
        assert len(result.blocking_failures) == 0

    def test_score_regression_fails(self):
        result = run_precheck(
            score_before=0.9,
            score_after=0.7,
            threshold=0.8,
            hypothesis="add new feature",
            history=[],
            project_path=Path("/tmp"),
        )
        assert not result.passed
        assert "score_direction" in result.blocking_failures

    def test_anti_pattern_fails(self):
        history = [
            {"id": 3, "hypothesis": "add structured logging to eval", "verdict": "revert"},
        ]
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="add structured logging to eval",
            history=history,
            project_path=Path("/tmp"),
        )
        assert not result.passed
        assert "anti_pattern" in result.blocking_failures

    def test_multiple_failures(self):
        history = [
            {"id": 3, "hypothesis": "add logging to eval runner", "verdict": "revert"},
        ]
        result = run_precheck(
            score_before=0.9,
            score_after=0.7,
            threshold=0.8,
            hypothesis="add logging to eval runner",
            history=history,
            project_path=Path("/tmp"),
        )
        assert not result.passed
        assert "score_direction" in result.blocking_failures
        assert "anti_pattern" in result.blocking_failures

    def test_fixed_surfaces_included(self, tmp_path):
        (tmp_path / "truth.py").write_text("def subtract(a, b): return a - b\n")
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="do not subtract from the values",
            history=[],
            project_path=tmp_path,
            baseline_sha="abc123",
            fixed_surfaces=["truth.py"],
        )
        check_names = [c.name for c in result.checks]
        assert "fixed_surfaces" in check_names

    def test_summary_output(self):
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="test",
            history=[],
            project_path=Path("/tmp"),
        )
        summary = result.summary()
        assert "PASS" in summary
        assert "score_direction" in summary


# ── review: format_review ─────────────────────────────────────


class TestFormatReview:
    def test_keep_review(self):
        payload = ReviewPayload(
            verdict="KEEP",
            reason="Score improved and all guards pass",
            score_before=0.75,
            score_after=0.85,
            threshold=0.8,
            guard_results={"scope": "PASS", "eval_immutable": "PASS"},
            precheck_summary="  PASS: score_direction\n  PASS: anti_pattern",
            code_notes=["Clean implementation", "Tests added"],
            experiment_id=42,
            hypothesis="Add structured logging",
        )
        body = format_review(payload)
        assert "KEEP" in body
        assert "0.7500" in body
        assert "0.8500" in body
        assert "+0.1000" in body
        assert "#42" in body
        assert "scope" in body
        assert "Clean implementation" in body
        assert "Factory CEO" in body

    def test_revert_review(self):
        payload = ReviewPayload(
            verdict="REVERT",
            reason="Score regressed",
            score_before=0.85,
            score_after=0.7,
            threshold=0.8,
            guard_results={"scope": "FAIL"},
            precheck_summary="  FAIL: score_direction",
            code_notes=[],
        )
        body = format_review(payload)
        assert "REVERT" in body
        assert "❌" in body
        assert "FAIL" in body

    def test_no_scores_omits_score_section(self):
        payload = ReviewPayload(
            verdict="REVERT",
            reason="Missing scores",
            score_before=None,
            score_after=None,
            threshold=0.8,
            guard_results={},
            precheck_summary="",
            code_notes=[],
        )
        body = format_review(payload)
        assert "Score Comparison" not in body

    def test_one_score_present_shows_section(self):
        payload = ReviewPayload(
            verdict="KEEP",
            reason="Partial scores",
            score_before=0.8,
            score_after=None,
            threshold=0.8,
            guard_results={},
            precheck_summary="",
            code_notes=[],
        )
        body = format_review(payload)
        assert "Score Comparison" in body
        assert "n/a" in body

    def test_minimal_payload(self):
        payload = ReviewPayload(
            verdict="KEEP",
            reason="All good",
            score_before=0.8,
            score_after=0.9,
            threshold=0.8,
            guard_results={},
            precheck_summary="",
            code_notes=[],
        )
        body = format_review(payload)
        assert "KEEP" in body
        assert "Guard Checks" not in body  # no guards = no section


# ── review: post_review ──────────────────────────────────────


class TestPostReview:
    @patch("factory.review.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert post_review(42, "body", "KEEP") is True
        call_args = mock_run.call_args[0][0]
        assert "--approve" in call_args
        assert "42" in call_args

    @patch("factory.review.subprocess.run")
    def test_revert_uses_request_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        post_review(42, "body", "REVERT")
        call_args = mock_run.call_args[0][0]
        assert "--request-changes" in call_args

    @patch("factory.review.subprocess.run")
    def test_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        post_review(42, "body", "KEEP", repo="owner/repo")
        call_args = mock_run.call_args[0][0]
        assert "--repo" in call_args
        assert "owner/repo" in call_args

    @patch("factory.review.subprocess.run")
    def test_review_fails_falls_back_to_comment(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="auth error"),
            MagicMock(returncode=0),
        ]
        assert post_review(42, "body", "KEEP") is True
        assert mock_run.call_count == 2
        fallback_cmd = mock_run.call_args_list[1][0][0]
        assert fallback_cmd[:3] == ["gh", "pr", "comment"]

    @patch("factory.review.subprocess.run")
    def test_review_and_comment_both_fail(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        assert post_review(42, "body", "KEEP") is False
        assert mock_run.call_count == 2

    @patch("factory.review.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        assert post_review(42, "body", "KEEP") is False

    @patch("factory.review.subprocess.run")
    def test_gh_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert post_review(42, "body", "KEEP") is False


# ── CLI integration ───────────────────────────────────────────


class TestCLIParser:
    def test_precheck_parser(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "precheck", "/tmp/test",
            "--score-before", "0.7",
            "--score-after", "0.85",
            "--hypothesis", "add logging",
            "--baseline", "abc123",
            "--similarity-threshold", "0.5",
        ])
        assert args.command == "precheck"
        assert args.score_before == 0.7
        assert args.score_after == 0.85
        assert args.hypothesis == "add logging"
        assert args.baseline == "abc123"
        assert args.similarity_threshold == 0.5

    def test_review_parser(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "review",
            "--verdict", "KEEP",
            "--reason", "All good",
            "--score-before", "0.7",
            "--score-after", "0.9",
            "--threshold", "0.8",
            "--guards", "scope:PASS,eval:PASS",
            "--experiment-id", "42",
            "--hypothesis", "add logging",
            "--pr", "99",
            "--repo", "owner/repo",
            "--dry-run",
        ])
        assert args.command == "review"
        assert args.verdict == "KEEP"
        assert args.pr == 99
        assert args.dry_run is True

    def test_review_parser_minimal(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["review", "--verdict", "revert"])
        assert args.command == "review"
        assert args.verdict == "revert"


# ── smoke test config parsing ─────────────────────────────────


class TestSmokeTestConfig:
    def test_model_field(self):
        from factory.models import FactoryConfig

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            smoke_test="curl -sf http://localhost:8000/health",
        )
        assert config.smoke_test == "curl -sf http://localhost:8000/health"

    def test_model_default_empty(self):
        from factory.models import FactoryConfig

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
        )
        assert config.smoke_test == ""

    def test_store_parses_smoke_test(self, tmp_path):
        factory_md = tmp_path / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n"
            "## Scope\n### Modifiable\n- src/**\n"
            "## Guards\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
            "## Smoke Test\n```bash\ncurl -sf http://localhost:8000/health\n```\n"
            "## Constraints\n"
        )
        (tmp_path / ".factory").mkdir()

        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_path)
        import asyncio
        config = asyncio.run(store.reparse_config())
        assert config.smoke_test == "curl -sf http://localhost:8000/health"

    def test_store_no_smoke_test(self, tmp_path):
        factory_md = tmp_path / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n"
            "## Scope\n### Modifiable\n- src/**\n"
            "## Guards\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
            "## Constraints\n"
        )
        (tmp_path / ".factory").mkdir()

        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_path)
        import asyncio
        config = asyncio.run(store.reparse_config())
        assert config.smoke_test == ""
