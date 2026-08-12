"""Tests for design_workflow_v2 — Phase 4 recomposed design mode using SubWorkflowNode."""

from __future__ import annotations

import pytest

from factory.workflow.definitions import (
    _get_builtin_registry,
    design_workflow,
    design_workflow_v2,
    register_all,
)
from factory.workflow.primitives import (
    AgentNode,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    SubWorkflowNode,
)


class TestDesignV2Validation:
    """design_workflow_v2 must pass graph validation."""

    def test_validates_cleanly(self) -> None:
        wf = design_workflow_v2()
        issues = wf.validate_graph()
        assert issues == [], f"design-v2 has issues: {issues}"

    def test_just_plan_validates_cleanly(self) -> None:
        wf = design_workflow_v2(just_plan=True)
        issues = wf.validate_graph()
        assert issues == [], f"design-v2 just_plan has issues: {issues}"


class TestDesignV2NodeCount:
    """design_workflow_v2 should have fewer total nodes than design_workflow (the point of decomposition)."""

    def test_fewer_nodes_than_original(self) -> None:
        original = design_workflow()
        v2 = design_workflow_v2()
        assert len(v2.nodes) < len(original.nodes), (
            f"v2 has {len(v2.nodes)} nodes, original has {len(original.nodes)} — "
            f"decomposition should reduce node count"
        )


class TestDesignV2SubWorkflowNodes:
    """design_workflow_v2 must contain SubWorkflowNode instances."""

    def test_has_sub_workflow_nodes(self) -> None:
        wf = design_workflow_v2()
        sub_nodes = [n for n in wf.nodes.values() if isinstance(n, SubWorkflowNode)]
        assert len(sub_nodes) >= 2, (
            f"Expected at least 2 SubWorkflowNode instances, found {len(sub_nodes)}"
        )

    def test_sub_research_references_research_pipeline(self) -> None:
        wf = design_workflow_v2()
        node = wf.nodes["sub_research"]
        assert isinstance(node, SubWorkflowNode)
        assert node.workflow_name == "research-pipeline"

    def test_sub_build_verify_references_build_verify(self) -> None:
        wf = design_workflow_v2()
        node = wf.nodes["sub_build_verify"]
        assert isinstance(node, SubWorkflowNode)
        assert node.workflow_name == "build-verify"

    def test_just_plan_has_no_build_verify(self) -> None:
        wf = design_workflow_v2(just_plan=True)
        assert "sub_build_verify" not in wf.nodes


class TestDesignV2ParallelObserve:
    """study and research must run in parallel via fork/join."""

    def test_has_fork_observe(self) -> None:
        wf = design_workflow_v2()
        assert "fork_observe" in wf.nodes
        assert isinstance(wf.nodes["fork_observe"], ForkNode)

    def test_has_join_observe(self) -> None:
        wf = design_workflow_v2()
        assert "join_observe" in wf.nodes
        assert isinstance(wf.nodes["join_observe"], JoinNode)

    def test_fork_targets_study_and_research(self) -> None:
        wf = design_workflow_v2()
        fork = wf.nodes["fork_observe"]
        assert isinstance(fork, ForkNode)
        assert "study" in fork.targets
        assert "sub_research" in fork.targets

    def test_join_sources_study_and_research(self) -> None:
        wf = design_workflow_v2()
        join = wf.nodes["join_observe"]
        assert isinstance(join, JoinNode)
        assert "study" in join.sources
        assert "sub_research" in join.sources


class TestDesignV2IOChain:
    """The IO contract chain must be valid across sub-workflows."""

    def test_research_outputs_feed_strategize_inputs(self) -> None:
        from factory.workflow.definitions import research_pipeline_workflow, strategize_workflow

        research = research_pipeline_workflow()
        strat = strategize_workflow(gate_type="user")
        assert research.io is not None
        assert strat.io is not None
        assert strat.io.inputs.issubset(research.io.outputs)

    def test_strategize_outputs_feed_build_verify_inputs(self) -> None:
        from factory.workflow.definitions import build_verify_workflow, strategize_workflow

        strat = strategize_workflow()
        bv = build_verify_workflow()
        assert strat.io is not None
        assert bv.io is not None
        assert bv.io.inputs.issubset(strat.io.outputs)

    def test_study_node_writes_observations(self) -> None:
        wf = design_workflow_v2()
        study = wf.nodes["study"]
        assert isinstance(study, Study)
        assert ".factory/strategy/observations.md" in study.writes

    def test_strategist_reads_research_combined(self) -> None:
        wf = design_workflow_v2()
        strategist = wf.nodes["strategist"]
        assert isinstance(strategist, AgentNode)
        assert ".factory/strategy/research-combined.md" in strategist.reads


class TestDesignV2Regression:
    """Existing design_workflow() must still validate after adding design_workflow_v2."""

    def test_original_design_workflow_still_valid(self) -> None:
        wf = design_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Original design_workflow has issues: {issues}"

    def test_original_design_workflow_just_plan_still_valid(self) -> None:
        wf = design_workflow(just_plan=True)
        issues = wf.validate_graph()
        assert issues == [], f"Original design_workflow(just_plan) has issues: {issues}"


class TestDesignV2Registration:
    """design_workflow_v2 must be registered and discoverable."""

    def test_registered_in_builtin_registry(self) -> None:
        registry = _get_builtin_registry()
        assert "design-v2" in registry

    def test_register_all_includes_design_v2(self) -> None:
        all_workflows = register_all()
        assert "design-v2" in all_workflows

    def test_strategize_user_registered(self) -> None:
        registry = _get_builtin_registry()
        assert "strategize-user" in registry

    def test_strategize_agent_registered(self) -> None:
        registry = _get_builtin_registry()
        assert "strategize-agent" in registry


class TestDesignV2Structure:
    """Verify key structural properties of the recomposed workflow."""

    def test_start_node_is_gate_has_factory(self) -> None:
        wf = design_workflow_v2()
        assert wf.start_node == "gate_has_factory"

    def test_name_is_design_v2(self) -> None:
        wf = design_workflow_v2()
        assert wf.name == "design-v2"

    def test_just_plan_name_is_plan_v2(self) -> None:
        wf = design_workflow_v2(just_plan=True)
        assert wf.name == "plan-v2"

    def test_just_plan_is_terminal(self) -> None:
        wf = design_workflow_v2(just_plan=True)
        assert wf.terminal is True

    def test_full_is_not_terminal(self) -> None:
        wf = design_workflow_v2()
        assert wf.terminal is False

    def test_has_gate_strategy_user_evaluator(self) -> None:
        wf = design_workflow_v2()
        gate = wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_has_archivist_plan_non_blocking(self) -> None:
        wf = design_workflow_v2()
        archivist = wf.nodes["archivist_plan"]
        assert isinstance(archivist, AgentNode)
        assert archivist.blocking is False

    def test_has_spec_generate_non_blocking(self) -> None:
        wf = design_workflow_v2()
        spec = wf.nodes["spec_generate"]
        assert isinstance(spec, FnNode)
        assert spec.blocking is False


class TestDesignV2DryRun:
    """Dry-run execution must succeed."""

    @pytest.fixture
    def project_path(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "strategy").mkdir(parents=True)
        (factory_dir / "reviews").mkdir(parents=True)
        (factory_dir / "experiments").mkdir(parents=True)
        (factory_dir / "archive").mkdir(parents=True)
        return tmp_path

    async def test_dry_run_succeeds(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = design_workflow_v2()
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None

    async def test_just_plan_dry_run_succeeds(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = design_workflow_v2(just_plan=True)
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None
