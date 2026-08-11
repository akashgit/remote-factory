"""Tests for improve-research and improve-v2 workflows (V2 FactoryContract migration)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from factory.workflow.definitions import (
    _get_builtin_registry,
    improve_research_workflow,
    improve_v2_workflow,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FactoryContract,
    GateNode,
    Study,
    VerdictType,
)


# ── improve_research_workflow ────────────────────────────────────


class TestImproveResearchWorkflow:
    def test_name(self) -> None:
        wf = improve_research_workflow()
        assert wf.name == "improve-research"

    def test_start_node(self) -> None:
        wf = improve_research_workflow()
        assert wf.start_node == "study"

    def test_nodes_present(self) -> None:
        wf = improve_research_workflow()
        assert "study" in wf.nodes
        assert "researcher" in wf.nodes
        assert "gate_research_internal" in wf.nodes
        assert len(wf.nodes) == 3

    def test_study_node_type(self) -> None:
        wf = improve_research_workflow()
        assert isinstance(wf.nodes["study"], Study)
        assert wf.nodes["study"].command == "factory study {project_path}"

    def test_researcher_node(self) -> None:
        wf = improve_research_workflow()
        researcher = wf.nodes["researcher"]
        assert isinstance(researcher, AgentNode)
        assert researcher.role == AgentRole.RESEARCHER
        assert ".factory/strategy/research-local.md" in researcher.writes
        assert ".factory/strategy/observations.md" in researcher.reads

    def test_gate_is_fn_type(self) -> None:
        wf = improve_research_workflow()
        gate = wf.nodes["gate_research_internal"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"
        assert gate.evaluator_role is None
        assert "eval_research_quality" in (gate.evaluator_command or "")

    def test_edges(self) -> None:
        wf = improve_research_workflow()
        edge_tuples = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("study", "researcher", None) in edge_tuples
        assert ("researcher", "gate_research_internal", None) in edge_tuples
        assert ("gate_research_internal", "researcher", VerdictType.RELOOP) in edge_tuples

    def test_no_proceed_edge_from_gate(self) -> None:
        wf = improve_research_workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_research_internal" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 0

    def test_validates(self) -> None:
        wf = improve_research_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"improve-research workflow has issues: {issues}"

    def test_registered_in_builtin_registry(self) -> None:
        registry = _get_builtin_registry()
        assert "improve-research" in registry
        wf = registry["improve-research"]()
        assert wf.name == "improve-research"


# ── improve_v2_workflow ──────────────────────────────────────────


class TestImproveV2Workflow:
    def test_name(self) -> None:
        wf = improve_v2_workflow()
        assert wf.name == "improve-v2"

    def test_start_node(self) -> None:
        wf = improve_v2_workflow()
        assert wf.start_node == "research_factory"

    def test_research_factory_is_factory_contract(self) -> None:
        wf = improve_v2_workflow()
        rf = wf.nodes["research_factory"]
        assert isinstance(rf, FactoryContract)

    def test_research_factory_input_contract(self) -> None:
        wf = improve_v2_workflow()
        rf = wf.nodes["research_factory"]
        assert isinstance(rf, FactoryContract)
        assert rf.input_contract == {
            "config": ".factory/config.json",
            "backlog": ".factory/strategy/backlog.md",
        }

    def test_research_factory_output_contract(self) -> None:
        wf = improve_v2_workflow()
        rf = wf.nodes["research_factory"]
        assert isinstance(rf, FactoryContract)
        assert rf.output_contract == {
            "research": ".factory/strategy/research-local.md",
            "observations": ".factory/strategy/observations.md",
        }

    def test_research_factory_eval_command(self) -> None:
        wf = improve_v2_workflow()
        rf = wf.nodes["research_factory"]
        assert isinstance(rf, FactoryContract)
        assert "eval_research_quality" in rf.eval_command

    def test_research_factory_transform(self) -> None:
        wf = improve_v2_workflow()
        rf = wf.nodes["research_factory"]
        assert isinstance(rf, FactoryContract)
        assert rf.transform == "improve-research"
        assert rf.transform_type == "workflow"

    def test_ceo_gate_at_v2_level(self) -> None:
        wf = improve_v2_workflow()
        gate = wf.nodes["gate_research"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_has_downstream_nodes(self) -> None:
        wf = improve_v2_workflow()
        assert "strategist" in wf.nodes
        assert "gate_strategy" in wf.nodes
        assert "begin" in wf.nodes
        assert "builder" in wf.nodes
        assert "gate_build" in wf.nodes
        assert "health_checker" in wf.nodes
        assert "code_reviewer" in wf.nodes
        assert "adversarial_tester" in wf.nodes
        assert "gate_qa" in wf.nodes
        assert "finalize" in wf.nodes
        assert "archivist" in wf.nodes

    def test_no_inline_study_or_researcher(self) -> None:
        wf = improve_v2_workflow()
        assert "study" not in wf.nodes
        assert "researcher" not in wf.nodes

    def test_edge_from_factory_to_gate(self) -> None:
        wf = improve_v2_workflow()
        edge_tuples = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("research_factory", "gate_research", None) in edge_tuples

    def test_reloop_goes_to_factory(self) -> None:
        wf = improve_v2_workflow()
        edge_tuples = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("gate_research", "research_factory", VerdictType.RELOOP) in edge_tuples

    def test_validates(self) -> None:
        wf = improve_v2_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"improve-v2 workflow has issues: {issues}"

    def test_registered_in_builtin_registry(self) -> None:
        registry = _get_builtin_registry()
        assert "improve-v2" in registry
        wf = registry["improve-v2"]()
        assert wf.name == "improve-v2"

    def test_trigger(self) -> None:
        from factory.models import ProjectState
        wf = improve_v2_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.NO_REPO, {})
        assert not wf.trigger(ProjectState.NO_FACTORY, {})


# ── Executor dry-run dispatch ────────────────────────────────────


class TestImproveResearchExecutorDryRun:
    def test_dry_run_standalone(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = improve_research_workflow()
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success is True
        assert result.nodes_executed >= 1

    def test_dry_run_v2_dispatches_contract(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = improve_v2_workflow()
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success is True
        assert "research_factory" in result.node_outputs
