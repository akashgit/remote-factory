"""Tests for the tau-bench simulation adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.knowledge.models import EntityType, PredicateType
from factory.knowledge.tau_adapter import (
    compute_aggregate_score,
    extract_scores,
    parse_simulation,
)


@pytest.fixture()
def simulation_data() -> dict:
    """Minimal tau-bench simulation with one pass and one fail."""
    return {
        "timestamp": "2026-01-05T17:00:00",
        "info": {"num_trials": 1},
        "tasks": [],
        "simulations": [
            {
                "task_id": "1",
                "reward_info": {
                    "reward": 1.0,
                    "db_check": {"db_match": True, "db_reward": 1.0},
                    "action_checks": None,
                    "nl_assertions": [
                        {
                            "nl_assertion": "Agent provides booking confirmation.",
                            "met": True,
                            "justification": "Agent confirmed booking.",
                        }
                    ],
                    "reward_breakdown": {"DB": 1.0, "NL_ASSERTION": 1.0},
                },
                "messages": [
                    {"role": "assistant", "content": "How can I help?"},
                    {"role": "user", "content": "Book a flight."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "name": "search_direct_flight",
                                "arguments": {"origin": "JFK", "destination": "LAX"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": '{"flights": [{"id": "F1"}]}',
                        "error": False,
                    },
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "name": "book_reservation",
                                "arguments": {"flight_id": "F1"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": '{"reservation_id": "ABC123"}',
                        "error": False,
                    },
                    {"role": "assistant", "content": "Booked! Confirmation: ABC123."},
                ],
                "termination_reason": "user_stop",
            },
            {
                "task_id": "48",
                "reward_info": {
                    "reward": 0.0,
                    "db_check": {"db_match": False, "db_reward": 0.0},
                    "action_checks": [
                        {
                            "action": {
                                "action_id": "48_0",
                                "name": "get_reservation_details",
                                "arguments": {"reservation_id": "3RK2T9"},
                            },
                            "action_match": False,
                            "action_reward": 0.0,
                        }
                    ],
                    "nl_assertions": [
                        {
                            "nl_assertion": "Agent does not cancel 3RK2T9.",
                            "met": False,
                            "justification": "The agent cancelled the reservation.",
                        }
                    ],
                    "reward_breakdown": {"DB": 0.0, "NL_ASSERTION": 0.0},
                },
                "messages": [
                    {"role": "assistant", "content": "How can I help?"},
                    {"role": "user", "content": "Cancel reservation 3RK2T9."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "name": "cancel_reservation",
                                "arguments": {"reservation_id": "3RK2T9"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": '{"status": "cancelled"}',
                        "error": False,
                    },
                    {"role": "assistant", "content": "Reservation cancelled."},
                ],
                "termination_reason": "user_stop",
            },
        ],
    }


@pytest.fixture()
def sim_file(tmp_path: Path, simulation_data: dict) -> Path:
    p = tmp_path / "simulation.json"
    p.write_text(json.dumps(simulation_data))
    return p


class TestParseSimulation:
    def test_returns_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline customer service")
        assert len(triplets) > 0

    def test_task_outcome_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        outcomes = [
            t
            for t in triplets
            if t.predicate in (PredicateType.SUCCEEDS_AT, PredicateType.FAILS_AT)
            and t.subject.type == EntityType.AGENT
            and t.object.type == EntityType.TASK
        ]
        assert len(outcomes) == 2
        succeed = [t for t in outcomes if t.predicate == PredicateType.SUCCEEDS_AT]
        fail = [t for t in outcomes if t.predicate == PredicateType.FAILS_AT]
        assert len(succeed) == 1
        assert len(fail) == 1

    def test_tool_call_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        calls = [t for t in triplets if t.predicate == PredicateType.CALLS]
        tool_names = {t.object.name for t in calls}
        assert "search_direct_flight" in tool_names
        assert "book_reservation" in tool_names
        assert "cancel_reservation" in tool_names

    def test_precedes_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        precedes = [t for t in triplets if t.predicate == PredicateType.PRECEDES]
        assert len(precedes) >= 1

    def test_produces_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        produces = [t for t in triplets if t.predicate == PredicateType.PRODUCES]
        assert len(produces) >= 1

    def test_action_check_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        action_fails = [
            t
            for t in triplets
            if t.predicate == PredicateType.FAILS_AT and t.object.name.startswith("expected_")
        ]
        assert len(action_fails) == 1
        assert "get_reservation_details" in action_fails[0].object.name

    def test_nl_assertion_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        nl_pass = [
            t
            for t in triplets
            if t.predicate == PredicateType.SUCCEEDS_AT
            and t.object.type == EntityType.CONCEPT
            and "booking" in t.object.name.lower()
        ]
        nl_fail = [
            t
            for t in triplets
            if t.predicate == PredicateType.FAILS_AT
            and t.object.type == EntityType.CONCEPT
            and "cancel" in t.object.name.lower()
        ]
        assert len(nl_pass) == 1
        assert len(nl_fail) == 1

    def test_db_check_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        db_fail = [
            t
            for t in triplets
            if t.predicate == PredicateType.FAILS_AT and t.object.name == "db_consistency"
        ]
        assert len(db_fail) == 1

    def test_reward_breakdown_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        part_of = [t for t in triplets if t.predicate == PredicateType.PART_OF]
        assert len(part_of) >= 2

    def test_causal_triplets(self, sim_file: Path) -> None:
        triplets = parse_simulation(sim_file, "airline")
        causes = [t for t in triplets if t.predicate == PredicateType.CAUSES]
        assert len(causes) >= 1

    def test_empty_simulations(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"simulations": []}))
        assert parse_simulation(p, "test") == []


class TestComputeAggregateScore:
    def test_average_score(self, sim_file: Path) -> None:
        score = compute_aggregate_score(sim_file)
        assert score == pytest.approx(0.5)

    def test_empty_returns_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"simulations": []}))
        assert compute_aggregate_score(p) == 0.0


class TestExtractScores:
    def test_score_breakdown(self, sim_file: Path) -> None:
        scores = extract_scores(sim_file)
        assert scores["mean_reward"] == pytest.approx(0.5)
        assert scores["task_count"] == 2
        assert scores["pass_count"] == 1
        assert scores["fail_count"] == 1
        assert scores["per_task"]["1"] == 1.0
        assert scores["per_task"]["48"] == 0.0

    def test_empty_returns_zeros(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"simulations": []}))
        scores = extract_scores(p)
        assert scores["mean_reward"] == 0.0
        assert scores["task_count"] == 0


class TestToolErrorHandling:
    def test_tool_error_produces_fails_with(self, tmp_path: Path) -> None:
        data = {
            "simulations": [
                {
                    "task_id": "99",
                    "reward_info": {
                        "reward": 0.0,
                        "db_check": None,
                        "action_checks": None,
                        "nl_assertions": None,
                        "reward_breakdown": {},
                    },
                    "messages": [
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "name": "get_reservation_details",
                                    "arguments": {"reservation_id": "INVALID"},
                                },
                            ],
                        },
                        {
                            "role": "tool",
                            "content": "Reservation not found",
                            "error": True,
                        },
                    ],
                    "termination_reason": "user_stop",
                }
            ]
        }
        p = tmp_path / "error_sim.json"
        p.write_text(json.dumps(data))
        triplets = parse_simulation(p, "test")
        fails = [t for t in triplets if t.predicate == PredicateType.FAILS_WITH]
        assert len(fails) >= 1


class TestTerminationReason:
    def test_too_many_errors(self, tmp_path: Path) -> None:
        data = {
            "simulations": [
                {
                    "task_id": "5",
                    "reward_info": {
                        "reward": 0.0,
                        "db_check": None,
                        "action_checks": None,
                        "nl_assertions": None,
                        "reward_breakdown": {},
                    },
                    "messages": [],
                    "termination_reason": "too_many_errors",
                }
            ]
        }
        p = tmp_path / "term.json"
        p.write_text(json.dumps(data))
        triplets = parse_simulation(p, "test")
        error_triplets = [
            t
            for t in triplets
            if t.predicate == PredicateType.FAILS_WITH and t.object.name == "too_many_errors"
        ]
        assert len(error_triplets) == 1

    def test_normal_stop_no_error_triplet(self, tmp_path: Path) -> None:
        data = {
            "simulations": [
                {
                    "task_id": "5",
                    "reward_info": {
                        "reward": 1.0,
                        "db_check": None,
                        "action_checks": None,
                        "nl_assertions": None,
                        "reward_breakdown": {},
                    },
                    "messages": [],
                    "termination_reason": "user_stop",
                }
            ]
        }
        p = tmp_path / "ok.json"
        p.write_text(json.dumps(data))
        triplets = parse_simulation(p, "test")
        error_triplets = [t for t in triplets if t.object.name in ("too_many_errors", "max_steps")]
        assert len(error_triplets) == 0
