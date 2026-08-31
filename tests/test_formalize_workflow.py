"""Tests for the formalize project-local workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from factory.models import ProjectState
from factory.workflow.primitives import VerdictType, Workflow
from factory.workflow.registry import WorkflowRegistry

_WF_PATH = Path(".factory/workflows/formalize.py")


def _load_formalize_workflow() -> Workflow:
    """Load the formalize workflow directly from the .py file."""
    spec = importlib.util.spec_from_file_location("formalize", _WF_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.workflow()


@pytest.fixture(autouse=True)
def _reset_registry():
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


# ── Graph validation ────────────────────────────────────────────


class TestGraphValidation:
    def test_formalize_graph_valid(self) -> None:
        """The formalize workflow graph has no structural issues."""
        wf = _load_formalize_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Graph validation issues: {issues}"


# ── Trigger function ────────────────────────────────────────────


class TestTrigger:
    def test_fires_on_formalize_mode(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "formalize"}) is True

    def test_does_not_fire_on_other_modes(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"}) is False

    def test_does_not_fire_on_empty_ctx(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False

    def test_does_not_fire_on_focus_alone(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"focus": "some algorithm"}) is False


# ── Node count and expected IDs ─────────────────────────────────


class TestNodeStructure:
    def test_node_count(self) -> None:
        wf = _load_formalize_workflow()
        assert len(wf.nodes) == 22

    def test_expected_node_ids(self) -> None:
        wf = _load_formalize_workflow()
        expected = {
            "fork_research", "researcher_patterns", "researcher_mathlib",
            "researcher_algorithm", "join_research", "gate_research",
            "strategist", "gate_strategy", "archivist_plan",
            "builder_theory", "gate_theory", "gate_theory_review",
            "builder_ir", "gate_ir", "fn_generate",
            "fork_qa", "fn_check_generated", "fn_test", "fn_proof_hygiene",
            "join_qa", "gate_qa", "archivist_build",
        }
        assert set(wf.nodes.keys()) == expected

    def test_edge_count(self) -> None:
        wf = _load_formalize_workflow()
        assert len(wf.edges) == 31


# ── Terminal flag and start node ────────────────────────────────


class TestTerminalAndStart:
    def test_terminal(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.terminal is True

    def test_start_node(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.start_node == "fork_research"

    def test_name(self) -> None:
        wf = _load_formalize_workflow()
        assert wf.name == "formalize"


# ── Reloop edges ────────────────────────────────────────────────


class TestReloopEdges:
    def test_reloop_edges_exist(self) -> None:
        wf = _load_formalize_workflow()
        reloops = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        reloop_map = {(e.source, e.target) for e in reloops}
        assert ("gate_theory", "builder_theory") in reloop_map
        assert ("gate_ir", "builder_ir") in reloop_map
        assert ("gate_qa", "builder_ir") in reloop_map
        assert ("gate_research", "fork_research") in reloop_map
        assert ("gate_strategy", "strategist") in reloop_map

    def test_reloop_count(self) -> None:
        wf = _load_formalize_workflow()
        reloops = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert len(reloops) == 5


# ── Registry discovery ──────────────────────────────────────────


class TestRegistryDiscovery:
    def test_discovered_via_project_path(self) -> None:
        project = Path(".")
        entries = WorkflowRegistry.discover(project_path=project)
        assert "formalize" in entries
        assert entries["formalize"].source == "project"

    def test_get_workflow_returns_valid(self) -> None:
        project = Path(".")
        WorkflowRegistry.discover(project_path=project)
        wf = WorkflowRegistry.get_workflow("formalize", project_path=project)
        assert wf is not None
        assert wf.name == "formalize"


# ── Meta dict ───────────────────────────────────────────────────


class TestMeta:
    def test_meta_has_required_keys(self) -> None:
        spec = importlib.util.spec_from_file_location("formalize", _WF_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "meta")
        assert isinstance(mod.meta, dict)
        assert "name" in mod.meta
        assert "description" in mod.meta
        assert mod.meta["name"] == "formalize"
