"""Tests for the evolve workflow definition."""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict
from pathlib import Path

import pytest

from factory.cycle_analyzer import CycleRecord
from factory.inner_loop import InnerLoop
from factory.workflow.definitions import evolve_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)
from factory.models import ProjectState


def _make_workflow(*node_ids: str) -> Workflow:
    """Create a minimal workflow with the given node IDs for testing."""
    nodes: dict[str, AgentNode] = {
        nid: AgentNode(id=nid, role=AgentRole.RESEARCHER)
        for nid in node_ids
    }
    edges = [
        Edge(source=node_ids[i], target=node_ids[i + 1])
        for i in range(len(node_ids) - 1)
    ] if len(node_ids) > 1 else []
    return Workflow(
        name="test",
        nodes=nodes,
        edges=edges,
        start_node=node_ids[0] if node_ids else "",
    )


class TestEvolveWorkflowStructure:
    """Test the evolve workflow graph structure."""

    def test_workflow_creation(self):
        """evolve_workflow() returns a valid Workflow with name='evolve'."""
        wf = evolve_workflow()
        assert wf.name == "evolve"
        assert wf.start_node == "baseline"

    def test_graph_validates(self):
        """Workflow graph passes structural validation (no dangling edges, unreachable nodes)."""
        wf = evolve_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_required_nodes_present(self):
        """All expected nodes exist in the workflow."""
        wf = evolve_workflow()
        expected_nodes = {
            "baseline", "researcher", "gate_research",
            "strategist", "gate_strategy", "begin", "pre_eval", "builder",
            "gate_build", "health_checker", "post_eval", "gate_eval",
            "finalize", "archivist", "gate_convergence",
            "archivist_final",
        }
        assert expected_nodes.issubset(set(wf.nodes.keys()))

    def test_node_types(self):
        """Verify each node has the correct type."""
        wf = evolve_workflow()
        assert isinstance(wf.nodes["baseline"], FnNode)
        assert isinstance(wf.nodes["researcher"], AgentNode)
        assert isinstance(wf.nodes["gate_research"], GateNode)
        assert isinstance(wf.nodes["strategist"], AgentNode)
        assert isinstance(wf.nodes["gate_strategy"], GateNode)
        assert isinstance(wf.nodes["begin"], FnNode)
        assert isinstance(wf.nodes["pre_eval"], FnNode)
        assert isinstance(wf.nodes["builder"], AgentNode)
        assert isinstance(wf.nodes["gate_build"], GateNode)
        assert isinstance(wf.nodes["health_checker"], AgentNode)
        assert isinstance(wf.nodes["post_eval"], FnNode)
        assert isinstance(wf.nodes["gate_eval"], GateNode)
        assert isinstance(wf.nodes["finalize"], FnNode)
        assert isinstance(wf.nodes["archivist"], AgentNode)
        assert isinstance(wf.nodes["gate_convergence"], GateNode)
        assert isinstance(wf.nodes["archivist_final"], AgentNode)

    def test_agent_roles(self):
        """Verify each AgentNode has the correct role."""
        wf = evolve_workflow()
        assert wf.nodes["researcher"].role == AgentRole.RESEARCHER
        assert wf.nodes["strategist"].role == AgentRole.STRATEGIST
        assert wf.nodes["builder"].role == AgentRole.BUILDER
        assert wf.nodes["health_checker"].role == AgentRole.HEALTH_CHECKER
        assert wf.nodes["archivist"].role == AgentRole.ARCHIVIST
        assert wf.nodes["archivist_final"].role == AgentRole.ARCHIVIST

    def test_archivist_non_blocking(self):
        """The mid-loop archivist is non-blocking (fire-and-forget)."""
        wf = evolve_workflow()
        assert wf.nodes["archivist"].blocking is False

    def test_archivist_final_blocking(self):
        """The final archivist is blocking (must complete before exit)."""
        wf = evolve_workflow()
        assert wf.nodes["archivist_final"].blocking is True

    def test_builder_timeout(self):
        """Builder has an extended timeout for code modification work."""
        wf = evolve_workflow()
        assert wf.nodes["builder"].timeout == 1200


class TestEvolveInnerLoopIntegration:
    """Tests for InnerLoop/CycleAnalyzer artifact production nodes."""

    def test_evolve_pre_eval_node_exists(self):
        """Gap 4: pre_eval FnNode copies current_score.json → eval_before.json."""
        wf = evolve_workflow()
        assert "pre_eval" in wf.nodes
        node = wf.nodes["pre_eval"]
        assert isinstance(node, FnNode)
        assert ".factory/experiments/$EXP_ID/eval_before.json" in node.writes
        assert ".factory/evolve/current_score.json" in node.reads

    def test_evolve_post_eval_node_exists(self):
        """Gap 2: post_eval FnNode emits eval.completed event."""
        wf = evolve_workflow()
        assert "post_eval" in wf.nodes
        node = wf.nodes["post_eval"]
        assert isinstance(node, FnNode)
        assert ".factory/events.jsonl" in node.writes

    def test_evolve_health_checker_dual_writes(self):
        """Gap 1: health_checker writes to BOTH review file and experiment dir."""
        wf = evolve_workflow()
        hc = wf.nodes["health_checker"]
        assert ".factory/reviews/health-check.md" in hc.writes
        assert ".factory/experiments/$EXP_ID/eval_after.json" in hc.writes

    def test_evolve_baseline_writes_exp000(self):
        """Gap 3: baseline declares experiment 000 artifact in writes."""
        wf = evolve_workflow()
        baseline = wf.nodes["baseline"]
        assert ".factory/experiments/000/eval_before.json" in baseline.writes

    def test_evolve_pre_eval_wiring(self):
        """pre_eval is wired between begin and builder."""
        wf = evolve_workflow()
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("begin", "pre_eval", None) in edges
        assert ("pre_eval", "builder", None) in edges
        assert ("begin", "builder", None) not in edges

    def test_evolve_post_eval_wiring(self):
        """post_eval is wired between health_checker and gate_eval."""
        wf = evolve_workflow()
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("health_checker", "post_eval", None) in edges
        assert ("post_eval", "gate_eval", None) in edges
        assert ("health_checker", "gate_eval", None) not in edges

    def test_evolve_node_count(self):
        """Evolve workflow has 16 nodes after adding pre_eval and post_eval."""
        wf = evolve_workflow()
        assert len(wf.nodes) == 16


class TestEvolveWorkflowEdges:
    """Test edge wiring and conditional routing."""

    def test_evolution_loop_exists(self):
        """There is a RELOOP edge from gate_convergence back to strategist."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_convergence"
            and e.target == "strategist"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1, "Missing convergence->strategist RELOOP edge"

    def test_convergence_proceed_to_final(self):
        """PROCEED from gate_convergence leads to archivist_final."""
        wf = evolve_workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_convergence"
            and e.target == "archivist_final"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_build_gate_reloop_to_builder(self):
        """gate_build RELOOP goes back to builder."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_build"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_strategy_gate_reloop_to_strategist(self):
        """gate_strategy RELOOP goes back to strategist."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_strategy"
            and e.target == "strategist"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_research_gate_reloop_to_researcher(self):
        """gate_research RELOOP goes back to researcher."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_research"
            and e.target == "researcher"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_eval_gate_proceeds_to_finalize(self):
        """gate_eval PROCEED goes to finalize."""
        wf = evolve_workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_eval"
            and e.target == "finalize"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1


class TestEvolveWorkflowTrigger:
    """Test the trigger function."""

    def test_trigger_on_evolve_mode(self):
        """Trigger fires when ctx.mode == 'evolve'."""
        wf = evolve_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "evolve"}) is True

    def test_trigger_false_for_other_modes(self):
        """Trigger does not fire for non-evolve modes."""
        wf = evolve_workflow()
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"}) is False
        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False

    def test_trigger_independent_of_state(self):
        """Trigger fires regardless of project state when mode is evolve."""
        wf = evolve_workflow()
        for state in ProjectState:
            assert wf.trigger(state, {"mode": "evolve"}) is True


class TestEvolveWorkflowRegistration:
    """Test workflow registration."""

    def test_registered_in_register_all(self):
        """evolve_workflow is included in register_all() output."""
        workflows = register_all()
        assert "evolve" in workflows
        assert workflows["evolve"].name == "evolve"


class TestEvolveWorkflowSkillExport:
    """Test SKILL.md generation."""

    def test_skill_export_succeeds(self):
        """workflow_to_skill_md produces valid output for evolve workflow."""
        from factory.workflow.skill_export import workflow_to_skill_md, validate_skill
        wf = evolve_workflow()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"Skill validation issues: {issues}"

    def test_skill_has_frontmatter(self):
        """Generated SKILL.md includes proper frontmatter."""
        from factory.workflow.skill_export import workflow_to_skill_md
        wf = evolve_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert skill_md.startswith("---")
        assert "workflow-evolve" in skill_md


# ── frozen_nodes tests ─────────────────────────────────────────


class TestFrozenNodesValidation:
    def test_invalid_ids_raise_value_error(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b", "c")
        with pytest.raises(ValueError, match="frozen_nodes contains IDs not in workflow.nodes"):
            InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["x", "y"]))

    def test_empty_is_valid(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset())
        assert loop.frozen_nodes == frozenset()

    def test_workflow_none_skips_validation(self, tmp_path: Path) -> None:
        loop = InnerLoop(tmp_path, frozen_nodes=frozenset(["nonexistent"]))
        assert loop.frozen_nodes == frozenset(["nonexistent"])

    def test_valid_ids_pass(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b", "c")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a", "b"]))
        assert loop.frozen_nodes == frozenset(["a", "b"])


class TestFrozenNodesOverFreeze:
    def test_all_nodes_frozen_emits_warning(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        with pytest.warns(UserWarning, match="All nodes are frozen"):
            InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a", "b"]))

    def test_partial_freeze_no_warning(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b", "c")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a"]))


class TestIsMutable:
    def test_unfrozen_is_mutable(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a"]))
        assert loop.is_mutable("b") is True

    def test_frozen_is_not_mutable(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a"]))
        assert loop.is_mutable("a") is False

    def test_unknown_raises_value_error(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf)
        with pytest.raises(ValueError, match="Unknown node ID"):
            loop.is_mutable("zzz")

    def test_workflow_none_returns_true(self, tmp_path: Path) -> None:
        loop = InnerLoop(tmp_path)
        assert loop.is_mutable("anything") is True


class TestMutableNodes:
    def test_correct_set_difference(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b", "c")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a"]))
        assert loop.mutable_nodes() == {"b", "c"}

    def test_empty_when_workflow_none(self, tmp_path: Path) -> None:
        loop = InnerLoop(tmp_path)
        assert loop.mutable_nodes() == set()

    def test_empty_when_all_frozen(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        with pytest.warns(UserWarning):
            loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a", "b"]))
        assert loop.mutable_nodes() == set()


class TestImmutableNodes:
    def test_returns_set_copy(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b", "c")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a", "b"]))
        result = loop.immutable_nodes()
        assert result == {"a", "b"}
        assert isinstance(result, set)

    def test_empty_when_none_frozen(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf)
        assert loop.immutable_nodes() == set()


class TestFrozenNodesDefault:
    def test_default_is_empty_frozenset(self, tmp_path: Path) -> None:
        loop = InnerLoop(tmp_path)
        assert loop.frozen_nodes == frozenset()
        assert isinstance(loop.frozen_nodes, frozenset)

    def test_backward_compatible_construction(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf)
        assert loop.frozen_nodes == frozenset()
        assert loop.workflow is wf


class TestWriteDirectivesFrozenNodes:
    def test_frozen_nodes_included_in_directives_file(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b", "c")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a", "c"]))
        loop._write_directives({"focus": "performance"})
        msg_path = tmp_path / ".factory" / "messages" / "outer-loop-0000.md"
        content = msg_path.read_text()
        assert "frozen_nodes" in content
        assert "a, c" in content

    def test_no_frozen_nodes_omits_key(self, tmp_path: Path) -> None:
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset())
        loop._write_directives({"focus": "performance"})
        msg_path = tmp_path / ".factory" / "messages" / "outer-loop-0000.md"
        content = msg_path.read_text()
        assert "frozen_nodes" not in content


class TestCollectResultsFrozenNodes:
    def test_collect_populates_frozen_and_mutable(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        wf = _make_workflow("a", "b", "c")
        loop = InnerLoop(tmp_path, workflow=wf, frozen_nodes=frozenset(["a"]))
        record = loop.collect()
        assert record.frozen_nodes == ["a"]
        assert sorted(record.mutable_node_ids) == ["b", "c"]

    def test_collect_empty_frozen(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        wf = _make_workflow("a", "b")
        loop = InnerLoop(tmp_path, workflow=wf)
        record = loop.collect()
        assert record.frozen_nodes == []
        assert sorted(record.mutable_node_ids) == ["a", "b"]


class TestCycleRecordSerialization:
    def test_default_fields_are_empty_lists(self) -> None:
        record = CycleRecord(
            cycle_number=0, mode="test", started_at=None,
            ended_at=None, duration_s=0,
            score_start=None, score_end=None, score_delta=None,
        )
        assert record.frozen_nodes == []
        assert record.mutable_node_ids == []

    def test_asdict_includes_new_fields(self) -> None:
        record = CycleRecord(
            cycle_number=1, mode="evolve", started_at=None,
            ended_at=None, duration_s=0,
            score_start=None, score_end=None, score_delta=None,
            frozen_nodes=["a", "c"],
            mutable_node_ids=["b"],
        )
        d = asdict(record)
        assert d["frozen_nodes"] == ["a", "c"]
        assert d["mutable_node_ids"] == ["b"]
        json.dumps(d, default=str)
