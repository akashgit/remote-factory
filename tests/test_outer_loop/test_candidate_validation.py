"""Tests for pre-evaluation validation against the mode contract."""

from __future__ import annotations

from factory.outer_loop.candidate_validation import CandidateValidator, ModeContract
from factory.outer_loop.mutations import remove_node
from factory.outer_loop.topology import apply_topology
from factory.workflow.primitives import Workflow


def _seed() -> Workflow:
    """A miniature produce -> consume mode with one knob on each side."""
    return Workflow.from_dict(
        {
            "name": "m",
            "nodes": {
                "start": {"id": "start", "_type": "FnNode", "command": "true",
                          "reads": [], "writes": ["task.yaml"]},
                "make": {"id": "make", "_type": "FnNode", "command": "true",
                         "reads": ["task.yaml"], "writes": ["candidate.py"]},
                "score": {"id": "score", "_type": "FnNode", "command": "true",
                          "reads": ["candidate.py"], "writes": ["score.json"]},
            },
            "edges": [
                {"source": "start", "target": "make", "condition": None},
                {"source": "make", "target": "score", "condition": None},
            ],
            "start_node": "start",
            "terminal": True,
            "knob_values": {"quality": 1},
            "knob_bounds": {"quality": [1, 2]},
            "knob_specs": {"quality": {"kind": "threshold", "node_id": "make", "default": 1}},
        }
    )


def test_contract_captures_interfaces_and_knob_owners() -> None:
    contract = ModeContract.from_workflow(_seed())
    assert "candidate.py" in contract.required_artifacts
    assert "task.yaml" in contract.required_artifacts
    assert "score.json" not in contract.required_artifacts  # produced but never consumed
    assert {"make", "score", "start"} >= contract.required_nodes
    assert contract.knob_nodes == {"quality": "make"}


def test_the_seed_satisfies_its_own_contract() -> None:
    seed = _seed()
    assert CandidateValidator(ModeContract.from_workflow(seed)).issues(seed) == []


def test_removing_the_producer_is_rejected_despite_tidy_declarations() -> None:
    seed = _seed()
    validator = CandidateValidator(ModeContract.from_workflow(seed))
    result = remove_node(seed, "make")
    assert result is not None, "removal itself is structurally allowed"
    candidate, _ = result
    # remove_node strips candidate.py from score's reads, so the graph stays
    # self-consistent; only the contract reveals the mode is broken.
    assert "candidate.py" not in candidate.nodes["score"].reads
    reasons = validator.issues(candidate)
    assert any("candidate.py" in r for r in reasons)
    assert any("quality" in r for r in reasons)  # knob lost its consumer


def test_legitimate_structural_change_is_accepted() -> None:
    seed = _seed()
    validator = CandidateValidator(ModeContract.from_workflow(seed))
    applied = apply_topology(seed, "make", "parallelize_next")
    assert applied is not None
    assert validator.issues(applied[0]) == []
