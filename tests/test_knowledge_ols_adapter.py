"""Tests for the OLS troubleshooting eval adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.knowledge.models import EntityType, PredicateType
from factory.knowledge.ols_adapter import (
    compute_aggregate_score,
    extract_scores,
    parse_results,
    write_failing_scenarios,
)


@pytest.fixture()
def single_turn_csv_data() -> str:
    """Minimal single-turn OLS eval CSV with one PASS scenario."""
    return (
        "conversation_group_id,turn_id,metric_identifier,score,result,"
        "reason,query,response,execution_time\n"
        "batch_failure,batch_failure,custom:answer_correctness,1.0,PASS,"
        '"Custom answer correctness: 1.00","What is the issue with job '
        'inventory-sync-validator","The job fails to connect to prod-db:3333",33.68\n'
    )


@pytest.fixture()
def multi_turn_csv_data() -> str:
    """Minimal multi-turn OLS eval CSV with per-turn and conversation metrics."""
    return (
        "conversation_group_id,turn_id,metric_identifier,score,result,"
        "reason,query,response,execution_time\n"
        "Network Policy Issue,Investigate the status of frontend application,"
        "custom:answer_correctness,0.35,FAIL,"
        '"Score below threshold","What is the status of the frontend application",'
        '"The frontend application is currently healthy",12.78\n'
        "Network Policy Issue,Investigate the status of frontend application,"
        "geval:generic_troubleshooting_experience,1.0,PASS,"
        '"Good troubleshooting","What is the status of the frontend application",'
        '"The frontend application is currently healthy",15.07\n'
        "Network Policy Issue,Why frontend is failing,"
        "custom:answer_correctness,1.0,PASS,"
        '"Correct root cause","Can you spot the root cause?",'
        '"The root cause is a misconfigured NetworkPolicy",92.60\n'
        "Network Policy Issue,Why frontend is failing,"
        "geval:generic_troubleshooting_experience,1.0,PASS,"
        '"Good analysis","Can you spot the root cause?",'
        '"The root cause is a misconfigured NetworkPolicy",93.20\n'
        "Network Policy Issue,,geval:troubleshooting_continuity,1.0,PASS,"
        '"Good continuity",,,"123.12\n'
        "Network Policy Issue,,deepeval:knowledge_retention,0.0,FAIL,"
        '"Failed retention",,,"125.44\n'
    )


@pytest.fixture()
def results_dir(tmp_path: Path, single_turn_csv_data: str, multi_turn_csv_data: str) -> Path:
    """Create a minimal results directory structure."""
    iter_dir = tmp_path / "iter_01"

    batch_dir = iter_dir / "batch_failure"
    batch_dir.mkdir(parents=True)
    (batch_dir / "evaluation_20260719_183036_detailed.csv").write_text(single_turn_csv_data)

    np_dir = iter_dir / "wrong_networkpolicy"
    np_dir.mkdir(parents=True)
    (np_dir / "evaluation_20260719_185126_detailed.csv").write_text(multi_turn_csv_data)

    return tmp_path


class TestParseResults:
    def test_returns_triplets(self, results_dir: Path) -> None:
        triplets = parse_results(results_dir, "k8s troubleshooting")
        assert len(triplets) > 0

    def test_scenario_outcome_triplets(self, results_dir: Path) -> None:
        triplets = parse_results(results_dir, "k8s troubleshooting")
        outcomes = [
            t
            for t in triplets
            if t.predicate in (PredicateType.SUCCEEDS_AT, PredicateType.FAILS_AT)
            and t.subject.type == EntityType.AGENT
            and t.object.type == EntityType.TASK
        ]
        assert len(outcomes) >= 2

    def test_metric_triplets(self, results_dir: Path) -> None:
        triplets = parse_results(results_dir, "k8s troubleshooting")
        part_of = [t for t in triplets if t.predicate == PredicateType.PART_OF]
        assert len(part_of) >= 1

    def test_conversation_metrics(self, results_dir: Path) -> None:
        triplets = parse_results(results_dir, "k8s troubleshooting")
        conv_triplets = [
            t
            for t in triplets
            if t.predicate == PredicateType.PART_OF
            and "conversation" in t.object.name.lower()
        ]
        assert len(conv_triplets) >= 1

    def test_causal_triplets_for_failures(self, results_dir: Path) -> None:
        triplets = parse_results(results_dir, "k8s troubleshooting")
        causes = [t for t in triplets if t.predicate == PredicateType.CAUSES]
        assert len(causes) >= 1

    def test_empty_results_dir(self, tmp_path: Path) -> None:
        assert parse_results(tmp_path, "test") == []


class TestComputeAggregateScore:
    def test_macro_average(self, results_dir: Path) -> None:
        score = compute_aggregate_score(results_dir)
        # batch_failure: 1/1 = 1.0, wrong_networkpolicy: 1/2 = 0.5
        # macro avg = (1.0 + 0.5) / 2 = 0.75
        assert score == pytest.approx(0.75)

    def test_empty_returns_zero(self, tmp_path: Path) -> None:
        assert compute_aggregate_score(tmp_path) == 0.0


class TestExtractScores:
    def test_detailed_breakdown(self, results_dir: Path) -> None:
        scores = extract_scores(results_dir)
        assert scores["scenario_count"] == 2
        assert "batch_failure" in scores["per_scenario"]
        assert "wrong_networkpolicy" in scores["per_scenario"]
        assert scores["per_scenario"]["batch_failure"] == 1.0
        assert scores["per_scenario"]["wrong_networkpolicy"] == pytest.approx(0.5)
        assert "per_metric" in scores
        assert "custom:answer_correctness" in scores["per_metric"]


class TestWriteFailingScenarios:
    def test_writes_failing_scenario_details(
        self, tmp_path: Path, results_dir: Path
    ) -> None:
        config = {
            "task_id": "ols_test",
            "agent_command": "",
            "task_context": "test",
            "results_dir": str(results_dir),
            "eval_command": "",
            "score_threshold": 0.5,
            "improvement_target": "",
            "scenarios": [],
        }
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_path = config_dir / "task_config.json"
        config_path.write_text(json.dumps(config))

        write_failing_scenarios(config_path)

        output = config_dir / "ols_test_failing_scenarios.md"
        assert output.exists()
        content = output.read_text()
        assert "wrong_networkpolicy" in content or "Network Policy Issue" in content


class TestErrorHandling:
    def test_malformed_csv(self, tmp_path: Path) -> None:
        """Malformed CSV should return empty list, not crash."""
        bad_dir = tmp_path / "iter_01" / "bad_scenario"
        bad_dir.mkdir(parents=True)
        (bad_dir / "evaluation_bad_detailed.csv").write_text(
            "not,a,valid,csv,header\nfoo,bar,baz\n"
        )
        triplets = parse_results(tmp_path, "test")
        assert triplets == []

    def test_missing_results_dir(self, tmp_path: Path) -> None:
        """Non-existent dir should return empty list."""
        missing = tmp_path / "nonexistent"
        triplets = parse_results(missing, "test")
        assert triplets == []

    def test_error_result_counts_as_fail(self, tmp_path: Path) -> None:
        """ERROR result rows should be treated as FAIL."""
        iter_dir = tmp_path / "iter_01" / "error_scenario"
        iter_dir.mkdir(parents=True)
        csv_content = (
            "conversation_group_id,turn_id,metric_identifier,score,result,"
            "reason,query,response,execution_time\n"
            "error_scenario,error_scenario,custom:answer_correctness,,ERROR,"
            '"Error occurred","Query","Response",10.0\n'
        )
        (iter_dir / "evaluation_error_detailed.csv").write_text(csv_content)
        score = compute_aggregate_score(tmp_path)
        assert score == 0.0
