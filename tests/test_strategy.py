"""Tests for factory.strategy — FEEC priority heuristic."""

from factory.strategy import FEECCategory, categorize_hypothesis, detect_stuck, rank_hypotheses


# ── categorize_hypothesis ────────────────────────────────────────────


class TestCategorizeHypothesis:
    def test_fix_keyword_error(self):
        assert categorize_hypothesis("Fix the import error in cli.py") == FEECCategory.FIX

    def test_fix_keyword_bug(self):
        assert categorize_hypothesis("There is a bug in the parser") == FEECCategory.FIX

    def test_fix_keyword_crash(self):
        assert categorize_hypothesis("The server crashes on startup") == FEECCategory.FIX

    def test_fix_keyword_fail(self):
        assert categorize_hypothesis("Tests fail after refactor") == FEECCategory.FIX

    def test_fix_keyword_regression(self):
        assert categorize_hypothesis("Regression in score after last change") == FEECCategory.FIX

    def test_fix_keyword_broken(self):
        assert categorize_hypothesis("Broken endpoint returns 500") == FEECCategory.FIX

    def test_fix_keyword_repair(self):
        assert categorize_hypothesis("Repair the database migration") == FEECCategory.FIX

    def test_exploit_keyword_improve(self):
        assert categorize_hypothesis("Improve test coverage") == FEECCategory.EXPLOIT

    def test_exploit_keyword_increase(self):
        assert categorize_hypothesis("Increase cache hit rate") == FEECCategory.EXPLOIT

    def test_exploit_keyword_extend(self):
        assert categorize_hypothesis("Extend the logging module") == FEECCategory.EXPLOIT

    def test_exploit_keyword_enhance(self):
        assert categorize_hypothesis("Enhance the CLI output") == FEECCategory.EXPLOIT

    def test_exploit_keyword_build_on(self):
        assert categorize_hypothesis("Build on the recent auth success") == FEECCategory.EXPLOIT

    def test_exploit_keyword_optimize(self):
        assert categorize_hypothesis("Optimize database queries") == FEECCategory.EXPLOIT

    def test_exploit_keyword_boost(self):
        assert categorize_hypothesis("Boost response time") == FEECCategory.EXPLOIT

    def test_combine_keyword_combine(self):
        assert categorize_hypothesis("Combine caching and batching") == FEECCategory.COMBINE

    def test_combine_keyword_merge(self):
        assert categorize_hypothesis("Merge auth and rbac modules") == FEECCategory.COMBINE

    def test_combine_keyword_integrate(self):
        assert categorize_hypothesis("Integrate logging with tracing") == FEECCategory.COMBINE

    def test_combine_keyword_unify(self):
        assert categorize_hypothesis("Unify the config parsers") == FEECCategory.COMBINE

    def test_combine_keyword_consolidate(self):
        assert categorize_hypothesis("Consolidate duplicate handlers") == FEECCategory.COMBINE

    def test_explore_default(self):
        assert categorize_hypothesis("Add a new REST endpoint") == FEECCategory.EXPLORE

    def test_explore_no_keywords(self):
        assert categorize_hypothesis("Rewrite the scheduler") == FEECCategory.EXPLORE

    def test_empty_text_defaults_to_explore(self):
        assert categorize_hypothesis("") == FEECCategory.EXPLORE

    def test_case_insensitive(self):
        assert categorize_hypothesis("FIX the CRASH now") == FEECCategory.FIX

    def test_fix_takes_priority_over_exploit(self):
        """When both FIX and EXPLOIT keywords appear, FIX wins."""
        assert categorize_hypothesis("Fix and improve the parser") == FEECCategory.FIX

    def test_exploit_takes_priority_over_combine(self):
        """When both EXPLOIT and COMBINE keywords appear, EXPLOIT wins."""
        assert categorize_hypothesis("Improve by combining modules") == FEECCategory.EXPLOIT

    def test_history_param_accepted(self):
        """history parameter is accepted (forward-compat) without error."""
        result = categorize_hypothesis("Fix a bug", history=[{"verdict": "revert"}])
        assert result == FEECCategory.FIX


# ── rank_hypotheses ──────────────────────────────────────────────────


class TestRankHypotheses:
    def test_sorts_by_feec_priority(self):
        hypotheses = [
            {"description": "Add a new endpoint"},
            {"description": "Fix the crash"},
            {"description": "Combine auth modules"},
            {"description": "Improve test coverage"},
        ]
        ranked = rank_hypotheses(hypotheses)
        categories = [h["category"] for h in ranked]
        assert categories == ["FIX", "EXPLOIT", "EXPLORE", "COMBINE"]

    def test_stable_sort_within_category(self):
        hypotheses = [
            {"description": "Fix the crash in login"},
            {"description": "Fix the error in signup"},
        ]
        ranked = rank_hypotheses(hypotheses)
        assert ranked[0]["description"] == "Fix the crash in login"
        assert ranked[1]["description"] == "Fix the error in signup"

    def test_empty_list(self):
        assert rank_hypotheses([]) == []

    def test_single_hypothesis(self):
        ranked = rank_hypotheses([{"description": "Add feature"}])
        assert len(ranked) == 1
        assert ranked[0]["category"] == "EXPLORE"

    def test_injects_category_key(self):
        ranked = rank_hypotheses([{"description": "Fix a bug"}])
        assert "category" in ranked[0]
        assert ranked[0]["category"] == "FIX"

    def test_all_same_category(self):
        hypotheses = [
            {"description": "Fix error A"},
            {"description": "Fix bug B"},
            {"description": "Fix crash C"},
        ]
        ranked = rank_hypotheses(hypotheses)
        assert all(h["category"] == "FIX" for h in ranked)
        # Order preserved
        assert ranked[0]["description"] == "Fix error A"
        assert ranked[2]["description"] == "Fix crash C"


# ── detect_stuck ─────────────────────────────────────────────────────


class TestDetectStuck:
    def test_stuck_three_consecutive_same_category(self):
        history = [
            {"hypothesis": "Fix error 1", "verdict": "revert"},
            {"hypothesis": "Fix crash 2", "verdict": "revert"},
            {"hypothesis": "Fix bug 3", "verdict": "revert"},
        ]
        assert detect_stuck(history) is True

    def test_not_stuck_different_categories(self):
        history = [
            {"hypothesis": "Fix error 1", "verdict": "revert"},
            {"hypothesis": "Improve coverage", "verdict": "revert"},
            {"hypothesis": "Fix crash 3", "verdict": "revert"},
        ]
        assert detect_stuck(history) is False

    def test_not_stuck_below_threshold(self):
        history = [
            {"hypothesis": "Fix error 1", "verdict": "revert"},
            {"hypothesis": "Fix crash 2", "verdict": "revert"},
        ]
        assert detect_stuck(history) is False

    def test_not_stuck_keep_breaks_streak(self):
        history = [
            {"hypothesis": "Fix error 1", "verdict": "revert"},
            {"hypothesis": "Fix crash 2", "verdict": "keep"},
            {"hypothesis": "Fix bug 3", "verdict": "revert"},
        ]
        assert detect_stuck(history) is False

    def test_empty_history(self):
        assert detect_stuck([]) is False

    def test_custom_threshold(self):
        history = [
            {"hypothesis": "Fix a", "verdict": "revert"},
            {"hypothesis": "Fix b", "verdict": "revert"},
        ]
        assert detect_stuck(history, threshold=2) is True

    def test_stuck_only_considers_tail(self):
        """Only the most recent consecutive reverts matter."""
        history = [
            {"hypothesis": "Add endpoint", "verdict": "keep"},
            {"hypothesis": "Fix error 1", "verdict": "revert"},
            {"hypothesis": "Fix crash 2", "verdict": "revert"},
            {"hypothesis": "Fix bug 3", "verdict": "revert"},
        ]
        assert detect_stuck(history) is True

    def test_not_stuck_when_mixed_verdicts_in_tail(self):
        history = [
            {"hypothesis": "Fix a", "verdict": "revert"},
            {"hypothesis": "Add feature", "verdict": "keep"},
            {"hypothesis": "Fix b", "verdict": "revert"},
            {"hypothesis": "Fix c", "verdict": "revert"},
        ]
        # Only last 2 are consecutive reverts
        assert detect_stuck(history) is False

    def test_missing_hypothesis_key(self):
        """Entries without hypothesis key default to EXPLORE."""
        history = [
            {"verdict": "revert"},
            {"verdict": "revert"},
            {"verdict": "revert"},
        ]
        # All default to EXPLORE -> stuck
        assert detect_stuck(history) is True
