"""Tests for factory.strategy — FEEC priority heuristic and tiered history."""

from factory.strategy import (
    FEECCategory,
    MAX_INLINE_HISTORY,
    _format_tier1,
    _format_tier2,
    _format_tier3,
    _record_to_dict,
    categorize_hypothesis,
    detect_stuck,
    format_tiered_history,
    rank_hypotheses,
)


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


# ── _format_tier1 ───────────────────────────────────────────────


def _make_record(exp_id: int, verdict: str = "keep", delta: float | None = 0.05,
                 hypothesis: str = "Add feature", change_summary: str = "Changed foo.py") -> dict:
    return {
        "id": exp_id,
        "verdict": verdict,
        "delta": delta,
        "hypothesis": hypothesis,
        "change_summary": change_summary,
    }


class TestFormatTier1:
    def test_full_detail_output(self):
        r = _make_record(42, "keep", 0.05, "Add caching layer", "Modified cache.py")
        out = _format_tier1(r)
        assert "Experiment 42" in out
        assert "[keep]" in out
        assert "+0.0500" in out
        assert "Add caching layer" in out
        assert "Modified cache.py" in out

    def test_no_delta(self):
        r = _make_record(1, "revert", None)
        out = _format_tier1(r)
        assert "n/a" in out

    def test_no_change_summary(self):
        r = _make_record(1, "keep", 0.01, change_summary="")
        out = _format_tier1(r)
        assert "Changes:" not in out

    def test_long_change_summary_truncated(self):
        r = _make_record(1, "keep", 0.01, change_summary="x" * 300)
        out = _format_tier1(r)
        assert len([line for line in out.split("\n") if "Changes:" in line][0]) < 250


# ── _format_tier2 ───────────────────────────────────────────────


class TestFormatTier2:
    def test_one_line_format(self):
        r = _make_record(7, "revert", -0.03, "Improve logging")
        out = _format_tier2(r)
        assert out.startswith("- #7")
        assert "revert" in out
        assert "-0.0300" in out
        assert "Improve logging" in out
        assert "\n" not in out

    def test_no_delta(self):
        r = _make_record(5, "keep", None, "Some change")
        out = _format_tier2(r)
        assert "n/a" in out

    def test_long_hypothesis_truncated(self):
        r = _make_record(1, "keep", 0.0, hypothesis="x" * 200)
        out = _format_tier2(r)
        assert len(out) < 200


# ── _format_tier3 ───────────────────────────────────────────────


class TestFormatTier3:
    def test_aggregate_stats(self):
        records = [
            _make_record(i, "keep" if i % 2 == 0 else "revert", 0.01 * i)
            for i in range(1, 6)
        ]
        out = _format_tier3(records)
        assert "5 older experiments" in out
        assert "keep rate" in out
        assert "Categories:" in out
        assert "Verdicts:" in out

    def test_empty_records(self):
        assert _format_tier3([]) == ""

    def test_keep_rate_calculation(self):
        records = [
            _make_record(1, "keep", 0.1),
            _make_record(2, "keep", 0.2),
            _make_record(3, "revert", -0.1),
            _make_record(4, "keep", 0.05),
        ]
        out = _format_tier3(records)
        assert "75%" in out

    def test_score_trajectory(self):
        records = [
            _make_record(1, "keep", -0.05),
            _make_record(2, "keep", 0.10),
        ]
        out = _format_tier3(records)
        assert "-0.0500" in out
        assert "+0.1000" in out

    def test_no_deltas(self):
        records = [_make_record(1, "keep", None)]
        out = _format_tier3(records)
        assert "trajectory" not in out


# ── format_tiered_history ───────────────────────────────────────


class TestFormatTieredHistory:
    def test_empty(self):
        out = format_tiered_history([])
        assert "No experiments" in out

    def test_single_record_tier1_only(self):
        records = [_make_record(1)]
        out = format_tiered_history(records)
        assert "Tier 1" in out
        assert "Tier 2" not in out
        assert "Tier 3" not in out
        assert "Experiment 1" in out

    def test_three_records_tier1_only(self):
        records = [_make_record(i) for i in range(1, 4)]
        out = format_tiered_history(records)
        assert "Tier 1" in out
        assert "Tier 2" not in out
        assert "Experiment 1" in out
        assert "Experiment 3" in out

    def test_five_records_tier1_and_tier2(self):
        records = [_make_record(i) for i in range(1, 6)]
        out = format_tiered_history(records)
        assert "Tier 1" in out
        assert "Tier 2" in out
        assert "Tier 3" not in out
        # Tier 1 has last 3
        assert "Experiment 3" in out
        assert "Experiment 5" in out
        # Tier 2 has first 2
        assert "#1" in out
        assert "#2" in out

    def test_ten_records_tier1_and_tier2(self):
        records = [_make_record(i) for i in range(1, 11)]
        out = format_tiered_history(records)
        assert "Tier 1" in out
        assert "Tier 2" in out
        assert "Tier 3" not in out
        assert "10 total" in out

    def test_fifteen_records_all_three_tiers(self):
        records = [
            _make_record(i, "keep" if i % 2 == 0 else "revert", 0.01 * i)
            for i in range(1, 16)
        ]
        out = format_tiered_history(records)
        assert "Tier 1" in out
        assert "Tier 2" in out
        assert "Tier 3" in out
        assert "15 total" in out
        # Tier 3 has the first 5 records (1-5), since capped=10 means records 6-15
        assert "5 older experiments" in out
        # Tier 1 has last 3 of capped (13, 14, 15)
        assert "Experiment 13" in out
        assert "Experiment 15" in out

    def test_inline_cap_at_max(self):
        """Only the last MAX_INLINE_HISTORY records appear in tier1+tier2."""
        records = [_make_record(i) for i in range(1, 25)]
        out = format_tiered_history(records)
        # Tier 1+2 should cover records 15-24 (last 10)
        # Records 1-14 should be in tier 3 aggregate
        assert "14 older experiments" in out
        # Record 15 should be in tier2 (one-line), not tier3
        assert "#15" in out

    def test_total_count_in_header(self):
        records = [_make_record(i) for i in range(1, 8)]
        out = format_tiered_history(records)
        assert "7 total" in out

    def test_accepts_object_records(self):
        """Records can be objects with attrs instead of dicts."""
        class FakeRecord:
            def __init__(self, exp_id: int):
                self.id = exp_id
                self.hypothesis = "Test hypothesis"
                self.verdict = "keep"
                self.delta = 0.05
                self.change_summary = "Changed test.py"

        records = [FakeRecord(i) for i in range(1, 4)]
        out = format_tiered_history(records)
        assert "Experiment 1" in out
        assert "Test hypothesis" in out


# ── _record_to_dict ─────────────────────────────────────────────


class TestRecordToDict:
    def test_dict_passthrough(self):
        d = {"id": 1, "hypothesis": "test"}
        assert _record_to_dict(d) is d

    def test_object_conversion(self):
        class Obj:
            id = 5
            hypothesis = "Fix bug"
            verdict = "keep"
            delta = 0.1
            change_summary = "Fixed it"

        result = _record_to_dict(Obj())
        assert result == {
            "id": 5,
            "hypothesis": "Fix bug",
            "verdict": "keep",
            "delta": 0.1,
            "change_summary": "Fixed it",
            "cost_usd": None,
        }

    def test_missing_attrs(self):
        class Minimal:
            pass

        result = _record_to_dict(Minimal())
        assert result["id"] == "?"
        assert result["hypothesis"] == ""


class TestMaxInlineHistory:
    def test_constant_value(self):
        assert MAX_INLINE_HISTORY == 10
