"""Tests for the reasoning optimizer: prompt, parsing, and legal edits."""

from __future__ import annotations

import json

from factory.outer_loop.autoresearch_optimizer import AutoresearchOptimizer, Edit
from factory.outer_loop.optimizers import OptimizerRegistry, ProposalContext
from factory.workflow.primitives import Workflow


def _workflow() -> Workflow:
    return Workflow.from_dict(
        {
            "name": "t",
            "nodes": {"n0": {"id": "n0", "_type": "FnNode", "command": "true"}},
            "edges": [],
            "start_node": "n0",
            "terminal": True,
            "knob_values": {"grid": 10.0, "mode": "strict"},
            "knob_bounds": {"grid": [4, 8, 12], "mode": ["strict", "lenient"]},
            "knob_specs": {
                "grid": {"kind": "threshold", "node_id": "n0", "default": 10},
                "mode": {"kind": "prompt", "node_id": "n0", "default": "strict"},
            },
        }
    )


SURFACE = [
    {"name": "grid", "kind": "threshold", "node_id": "n0", "value": 10.0,
     "bounds": [4, 8, 12], "expandable": True},
    {"name": "mode", "kind": "prompt", "node_id": "n0", "value": "strict",
     "bounds": ["strict", "lenient"], "expandable": False},
]


def test_registered_under_its_name() -> None:
    assert "autoresearch" in OptimizerRegistry.names()


def test_parse_tolerates_fenced_json_and_prose() -> None:
    raw = 'Reasoning...\n```json\n{"diagnosis": "d", "proposals": [{"knob": "grid", "value": 8}]}\n```'
    diagnosis, edits = AutoresearchOptimizer.parse(raw)
    assert diagnosis == "d"
    assert [(e.knob, e.value) for e in edits] == [("grid", 8)]


def test_parse_rejects_garbage_without_raising() -> None:
    assert AutoresearchOptimizer.parse("no json at all") == ("", [])


def _surface_map() -> dict[str, dict[str, object]]:
    return {str(k["name"]): k for k in SURFACE}


def test_edit_within_bounds_is_applied() -> None:
    applied = AutoresearchOptimizer._apply_edit(_workflow(), Edit("grid", 8), _surface_map())
    assert applied is not None
    candidate, change = applied
    assert candidate.knob_values["grid"] == 8
    assert "10.0 -> 8" in change


def test_edit_outside_closed_bounds_is_rejected() -> None:
    assert AutoresearchOptimizer._apply_edit(_workflow(), Edit("mode", "wild"), _surface_map()) is None


def test_edit_beyond_bounds_is_allowed_only_when_expandable() -> None:
    applied = AutoresearchOptimizer._apply_edit(_workflow(), Edit("grid", 40), _surface_map())
    assert applied is not None
    candidate, _ = applied
    assert candidate.knob_values["grid"] == 40
    # The new value joins the domain so later generations can select it again.
    assert 40 in candidate.knob_bounds["grid"]


def test_edit_on_undeclared_knob_is_rejected() -> None:
    assert AutoresearchOptimizer._apply_edit(_workflow(), Edit("nope", 1), _surface_map()) is None


def test_topology_knob_is_deferred_not_misapplied() -> None:
    surface = {"t": {"name": "t", "kind": "topology", "node_id": "n0", "bounds": [], "expandable": True}}
    assert AutoresearchOptimizer._apply_edit(_workflow(), Edit("t", "x"), surface) is None


def test_propose_records_prediction_and_confidence() -> None:
    class Stub:
        def complete(self, prompt: str, *, system: str = "") -> str:
            assert "Recent candidate runs" in prompt and "grid" in prompt
            return json.dumps(
                {
                    "diagnosis": "grid saturated",
                    "proposals": [
                        {"knob": "grid", "value": 12, "rationale": "probe",
                         "prediction": "+0.05", "confidence": 0.4}
                    ],
                }
            )

    ctx = ProposalContext(
        generation=1, sample_parent=lambda: ("p1", _workflow()), knob_surface=SURFACE
    )
    proposals = AutoresearchOptimizer(Stub()).propose(ctx, 2)  # type: ignore[arg-type]
    assert len(proposals) == 1
    p = proposals[0]
    assert p.optimizer == "autoresearch"
    assert p.parent_id == "p1"
    assert p.after["prediction"] == "+0.05"
    assert p.after["confidence"] == 0.4
    assert p.workflow.knob_values["grid"] == 12


def test_propose_survives_a_model_failure() -> None:
    class Broken:
        def complete(self, prompt: str, *, system: str = "") -> str:
            raise RuntimeError("transport down")

    ctx = ProposalContext(generation=1, sample_parent=lambda: ("p1", _workflow()), knob_surface=SURFACE)
    assert AutoresearchOptimizer(Broken()).propose(ctx, 2) == []  # type: ignore[arg-type]
