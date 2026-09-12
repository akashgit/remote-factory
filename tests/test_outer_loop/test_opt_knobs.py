"""Tests for deriving a mode's optimizable surface from the mode itself."""

from __future__ import annotations

from factory.outer_loop.autoresearch_optimizer import AutoresearchOptimizer, Edit
from factory.workflow.opt_knobs import FIELD_SPECS, mode_parameters
from factory.workflow.primitives import Workflow


def _mode() -> Workflow:
    return Workflow.from_dict(
        {
            "name": "m",
            "nodes": {
                "make": {
                    "id": "make", "_type": "LLMNode", "blocking": True,
                    "reads": [], "writes": ["candidate.py"],
                    "system_prompt": "write a candidate", "temperature": 0.7,
                    "timeout": 600.0, "model": "",
                },
                "check": {
                    "id": "check", "_type": "GateNode", "blocking": True,
                    "reads": [], "writes": [], "evaluator_type": "fn",
                    "evaluator_command": "true", "max_iterations": 500,
                },
            },
            "edges": [{"source": "make", "target": "check", "condition": None}],
            "start_node": "make", "terminal": True,
            "knob_values": {"strictness": "high"},
            "knob_bounds": {"strictness": ["high", "low"]},
            "knob_specs": {"strictness": {"kind": "prompt", "node_id": "check",
                                          "default": "high", "bounds": ["high", "low"]}},
        }
    )


def test_surface_is_derived_from_the_nodes() -> None:
    names = mode_parameters(_mode()).names()
    assert "make.system_prompt" in names, "a node field must become a knob"
    assert "make.timeout" in names
    assert "check.max_iterations" in names
    assert "strictness" in names  # graph-level knob carried through


def test_a_declared_but_unread_field_is_reported_not_advertised() -> None:
    """A graph can express a parameter no forward pass reads. PyTorch cannot."""
    params = mode_parameters(_mode())
    assert "make.temperature" not in params.names()
    assert "make.temperature" in {d["name"] for d in params.dead}
    assert any("temperature" in line for line in params.report())


def test_unverified_fields_are_reported_but_never_sold_as_knobs() -> None:
    params = mode_parameters(_mode())
    unverified = {u["name"] for u in params.unverified}
    assert unverified & {"make.tools", "make.max_tokens"}
    assert not (unverified & set(params.names()))


def test_every_advertised_field_names_its_consumer() -> None:
    for name, spec in FIELD_SPECS.items():
        if spec.status == "consumed":
            assert spec.consumed_by, f"{name} claims consumption without naming a consumer"
        if spec.status == "dead":
            assert not spec.consumed_by, f"{name} is dead but names a consumer"


def test_node_field_knob_writes_the_node_not_just_knob_values() -> None:
    surface = {"make.timeout": {"name": "make.timeout", "kind": "threshold",
                                "node_id": "make", "value": 600.0, "bounds": [], "expandable": True}}
    applied = AutoresearchOptimizer._apply_edit(_mode(), Edit("make.timeout", 1200), surface)
    assert applied is not None
    candidate, _change = applied
    assert candidate.nodes["make"].timeout == 1200.0
    assert candidate.nodes["check"].max_iterations == 500  # untouched


def test_node_field_knob_on_a_missing_field_is_refused() -> None:
    surface = {"make.nonexistent": {"name": "make.nonexistent", "kind": "threshold",
                                    "node_id": "make", "value": 1, "bounds": [], "expandable": True}}
    assert AutoresearchOptimizer._apply_edit(_mode(), Edit("make.nonexistent", 2), surface) is None


def test_prompt_knob_still_reaches_the_node() -> None:
    surface = {"make.system_prompt": {"name": "make.system_prompt", "kind": "prompt",
                                      "node_id": "make", "value": "old",
                                      "bounds": [], "expandable": True}}
    applied = AutoresearchOptimizer._apply_edit(_mode(), Edit("make.system_prompt", "new text"), surface)
    assert applied is not None
    assert applied[0].nodes["make"].system_prompt == "new text"
