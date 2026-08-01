"""Unit tests for the statefulness eval statistical analysis module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from analyze import (  # noqa: E402
    bootstrap_ci,
    cohens_d,
    descriptive_stats,
    load_results,
    wilcoxon_test,
)


class TestCohensD:
    def test_identical_arrays(self) -> None:
        vals = [5.0, 5.0, 5.0, 5.0, 5.0]
        assert cohens_d(vals, vals) == 0.0

    def test_large_difference(self) -> None:
        control = [1.0, 2.0, 3.0, 4.0, 5.0]
        treatment = [10.0, 11.0, 12.0, 13.0, 14.0]
        d = cohens_d(control, treatment)
        assert d is not None
        assert d > 0.8

    def test_negative_effect(self) -> None:
        control = [10.0, 11.0, 12.0, 13.0, 14.0]
        treatment = [1.0, 2.0, 3.0, 4.0, 5.0]
        d = cohens_d(control, treatment)
        assert d is not None
        assert d < -0.8

    def test_empty_control(self) -> None:
        assert cohens_d([], [1.0, 2.0]) is None

    def test_empty_treatment(self) -> None:
        assert cohens_d([1.0, 2.0], []) is None

    def test_single_element_control(self) -> None:
        assert cohens_d([1.0], [2.0, 3.0]) is None

    def test_single_element_treatment(self) -> None:
        assert cohens_d([1.0, 2.0], [3.0]) is None

    def test_zero_variance(self) -> None:
        assert cohens_d([5.0, 5.0], [5.0, 5.0]) == 0.0


class TestBootstrapCI:
    def test_large_effect_excludes_zero(self) -> None:
        control = [1.0, 2.0, 3.0, 4.0, 5.0]
        treatment = [20.0, 21.0, 22.0, 23.0, 24.0]
        ci = bootstrap_ci(control, treatment, n_resamples=5000)
        assert ci is not None
        low, high = ci
        assert low > 0, "CI lower bound should be above zero for a large effect"
        assert high > low

    def test_insufficient_data(self) -> None:
        assert bootstrap_ci([1.0], [2.0]) is None
        assert bootstrap_ci([], [1.0, 2.0]) is None

    def test_identical_returns_ci_around_zero(self) -> None:
        vals = [5.0, 5.0, 5.0, 5.0, 5.0]
        ci = bootstrap_ci(vals, vals, n_resamples=1000)
        assert ci is not None
        low, high = ci
        assert low <= 0.0 <= high


class TestWilcoxonTest:
    def test_too_few_samples(self) -> None:
        assert wilcoxon_test([1.0, 2.0], [3.0, 4.0]) is None
        assert wilcoxon_test([1.0], [2.0]) is None

    def test_mismatched_lengths(self) -> None:
        assert wilcoxon_test([1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0]) is None

    def test_all_zero_diffs(self) -> None:
        vals = [5.0, 5.0, 5.0, 5.0, 5.0]
        assert wilcoxon_test(vals, vals) is None

    def test_returns_statistic_and_pvalue(self) -> None:
        # n=5 is the minimum; Wilcoxon's smallest possible p is 0.0625
        control = [1.0, 2.0, 3.0, 4.0, 5.0]
        treatment = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = wilcoxon_test(control, treatment)
        assert result is not None
        stat, p_value = result
        assert p_value <= 0.0625
        assert stat >= 0


class TestDescriptiveStats:
    def test_known_data(self) -> None:
        values = [2.0, 4.0, 6.0, 8.0, 10.0]
        result = descriptive_stats(values)
        assert result["n"] == 5
        assert result["median"] == 6.0
        assert result["mean"] == 6.0
        assert result["min"] == 2.0
        assert result["max"] == 10.0
        assert result["stddev"] is not None
        assert result["stddev"] == pytest.approx(3.1623, abs=0.001)

    def test_empty_list(self) -> None:
        result = descriptive_stats([])
        assert result["n"] == 0
        assert result["median"] is None
        assert result["mean"] is None

    def test_single_element(self) -> None:
        result = descriptive_stats([42.0])
        assert result["n"] == 1
        assert result["median"] == 42.0
        assert result["mean"] == 42.0
        assert result["stddev"] == 0.0


class TestLoadResults:
    def test_loads_json_files(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "proj-a" / "control"
        proj_dir.mkdir(parents=True)

        data = {
            "project": "proj-a",
            "condition": "control",
            "iteration": 1,
            "exit_code": 0,
            "duration_s": 45.2,
            "metrics": {
                "factory_read_count": 3,
                "factory_files_read": [".factory/config.json", ".factory/results.tsv"],
                "agent_reinvocations": 1,
                "time_to_first_meaningful_action_s": 12.5,
                "total_tool_calls": 42,
            },
        }
        (proj_dir / "iter-1.json").write_text(json.dumps(data))

        results = load_results(tmp_path)
        assert len(results) == 1
        r = results[0]
        assert r.project == "proj-a"
        assert r.condition == "control"
        assert r.iteration == 1
        assert r.exit_code == 0
        assert r.duration_s == 45.2
        assert r.metrics["factory_read_count"] == 3
        assert r.metrics["factory_files_read_count"] == 2

    def test_empty_directory(self, tmp_path: Path) -> None:
        results = load_results(tmp_path)
        assert results == []

    def test_multiple_iterations(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "proj-b" / "treatment"
        proj_dir.mkdir(parents=True)

        for i in range(1, 4):
            data = {
                "project": "proj-b",
                "condition": "treatment",
                "iteration": i,
                "exit_code": 0,
                "duration_s": 30.0 + i,
                "metrics": {
                    "factory_read_count": i,
                    "total_tool_calls": 10 * i,
                },
            }
            (proj_dir / f"iter-{i}.json").write_text(json.dumps(data))

        results = load_results(tmp_path)
        assert len(results) == 3
        assert [r.iteration for r in results] == [1, 2, 3]

    def test_loads_events_field(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "proj-c" / "control"
        proj_dir.mkdir(parents=True)

        data = {
            "project": "proj-c",
            "condition": "control",
            "iteration": 1,
            "exit_code": 0,
            "duration_s": 60.0,
            "metrics": {"factory_read_count": 0, "total_tool_calls": 0},
            "events": {"agent_starts": 4, "agent_completions": 2, "agents": []},
        }
        (proj_dir / "iter-1.json").write_text(json.dumps(data))

        results = load_results(tmp_path)
        assert len(results) == 1
        assert results[0].events["agent_starts"] == 4
        assert results[0].events["agent_completions"] == 2

    def test_missing_events_defaults_to_empty(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "proj-d" / "treatment"
        proj_dir.mkdir(parents=True)

        data = {
            "project": "proj-d",
            "condition": "treatment",
            "iteration": 1,
            "exit_code": 0,
            "duration_s": 30.0,
            "metrics": {"factory_read_count": 0},
        }
        (proj_dir / "iter-1.json").write_text(json.dumps(data))

        results = load_results(tmp_path)
        assert results[0].events == {}


def _make_iteration_json(
    project: str,
    condition: str,
    iteration: int,
    agent_starts: int,
    duration_s: float = 120.0,
) -> dict:
    """Helper to create an iteration result dict with events data."""
    return {
        "project": project,
        "condition": condition,
        "iteration": iteration,
        "exit_code": 142,
        "duration_s": duration_s,
        "metrics": {
            "factory_read_count": 0,
            "factory_files_read": [],
            "agent_reinvocations": 0,
            "time_to_first_meaningful_action_s": None,
            "total_tool_calls": 0,
        },
        "events": {
            "agent_starts": agent_starts,
            "agent_completions": 0,
            "agents": [],
        },
    }


class TestAnalyzeWithEvents:
    def test_events_metrics_in_analysis(self, tmp_path: Path) -> None:
        from analyze import EVENTS_METRICS, analyze

        for cond in ("control", "treatment"):
            d = tmp_path / "proj" / cond
            d.mkdir(parents=True)
            for i in range(1, 6):
                starts = 2 if cond == "control" else 4
                data = _make_iteration_json("proj", cond, i, starts)
                (d / f"iter-{i}.json").write_text(json.dumps(data))

        result = analyze(tmp_path)
        assert "events_metrics" in result
        assert "stream_metrics" in result
        for m in EVENTS_METRICS:
            assert m in result["events_metrics"]
        agent_starts = result["events_metrics"]["agent_starts"]
        assert agent_starts["control"]["mean"] == 2.0
        assert agent_starts["treatment"]["mean"] == 4.0
        assert agent_starts["cohens_d"] is not None

    def test_stream_metrics_still_present(self, tmp_path: Path) -> None:
        from analyze import STREAM_METRICS, analyze

        for cond in ("control", "treatment"):
            d = tmp_path / "proj" / cond
            d.mkdir(parents=True)
            for i in range(1, 3):
                data = _make_iteration_json("proj", cond, i, 2)
                (d / f"iter-{i}.json").write_text(json.dumps(data))

        result = analyze(tmp_path)
        for m in STREAM_METRICS:
            assert m in result["stream_metrics"]

    def test_generate_report_includes_events_section(self, tmp_path: Path) -> None:
        from analyze import analyze, generate_report

        for cond in ("control", "treatment"):
            d = tmp_path / "proj" / cond
            d.mkdir(parents=True)
            for i in range(1, 6):
                starts = 2 if cond == "control" else 6
                data = _make_iteration_json("proj", cond, i, starts, duration_s=100.0 + i)
                (d / f"iter-{i}.json").write_text(json.dumps(data))

        analysis = analyze(tmp_path)
        report = generate_report(analysis)
        assert "Events-Based Metrics (Primary)" in report
        assert "Agent Starts (events.jsonl)" in report
        assert "Stream-JSON Metrics (Deprecated)" in report

    def test_per_project_events_in_analysis(self, tmp_path: Path) -> None:
        from analyze import analyze

        for cond in ("control", "treatment"):
            d = tmp_path / "proj-x" / cond
            d.mkdir(parents=True)
            for i in range(1, 6):
                starts = 3 if cond == "control" else 7
                data = _make_iteration_json("proj-x", cond, i, starts)
                (d / f"iter-{i}.json").write_text(json.dumps(data))

        result = analyze(tmp_path)
        assert "per_project_events" in result
        assert "proj-x" in result["per_project_events"]
        pp = result["per_project_events"]["proj-x"]
        assert "agent_starts" in pp
        assert pp["agent_starts"]["control"]["mean"] == 3.0
        assert pp["agent_starts"]["treatment"]["mean"] == 7.0
