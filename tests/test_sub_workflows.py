"""Tests for Phase 3 sub-workflows — reusable building blocks extracted from monolithic workflows."""

from __future__ import annotations

import pytest

from factory.workflow.definitions import (
    _get_builtin_registry,
    build_verify_workflow,
    build_workflow,
    deep_qa_workflow,
    design_workflow,
    improve_workflow,
    precheck_finalize_workflow,
    register_all,
    research_pipeline_workflow,
    strategize_workflow,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
)


# ── Graph validation ──────────────────────────────────────────────


class TestSubWorkflowValidation:
    """Each sub-workflow must pass validate_graph() with no issues."""

    def test_research_pipeline_valid(self) -> None:
        wf = research_pipeline_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"research-pipeline has issues: {issues}"

    def test_strategize_agent_valid(self) -> None:
        wf = strategize_workflow(gate_type="agent")
        issues = wf.validate_graph()
        assert issues == [], f"strategize(agent) has issues: {issues}"

    def test_strategize_user_valid(self) -> None:
        wf = strategize_workflow(gate_type="user")
        issues = wf.validate_graph()
        assert issues == [], f"strategize(user) has issues: {issues}"

    def test_deep_qa_valid(self) -> None:
        wf = deep_qa_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"deep-qa has issues: {issues}"

    def test_build_verify_valid(self) -> None:
        wf = build_verify_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"build-verify has issues: {issues}"

    def test_precheck_finalize_valid(self) -> None:
        wf = precheck_finalize_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"precheck-finalize has issues: {issues}"


# ── IO contracts ──────────────────────────────────────────────────


class TestSubWorkflowIO:
    """Each sub-workflow must have io defined with correct inputs/outputs."""

    def test_research_pipeline_io(self) -> None:
        wf = research_pipeline_workflow()
        assert wf.io is not None
        assert wf.io.inputs == set()
        assert ".factory/strategy/research-combined.md" in wf.io.outputs
        assert ".factory/archive/" in wf.io.optional_inputs
        assert ".factory/strategy/research-similar.md" in wf.io.optional_outputs
        assert ".factory/strategy/research-techstack.md" in wf.io.optional_outputs
        assert ".factory/strategy/research-pitfalls.md" in wf.io.optional_outputs

    def test_strategize_io(self) -> None:
        wf = strategize_workflow()
        assert wf.io is not None
        assert ".factory/strategy/research-combined.md" in wf.io.inputs
        assert ".factory/strategy/current.md" in wf.io.outputs

    def test_deep_qa_io(self) -> None:
        wf = deep_qa_workflow()
        assert wf.io is not None
        assert ".factory/reviews/builder-latest.md" in wf.io.inputs
        assert ".factory/strategy/current.md" in wf.io.inputs
        assert ".factory/reviews/health-check.md" in wf.io.outputs
        assert ".factory/reviews/code-review.md" in wf.io.outputs
        assert ".factory/reviews/adversarial-qa.md" in wf.io.outputs

    def test_build_verify_io(self) -> None:
        wf = build_verify_workflow()
        assert wf.io is not None
        assert ".factory/strategy/current.md" in wf.io.inputs
        assert ".factory/reviews/builder-latest.md" in wf.io.outputs
        assert ".factory/reviews/health-check.md" in wf.io.outputs
        assert ".factory/reviews/code-review.md" in wf.io.outputs
        assert ".factory/reviews/adversarial-qa.md" in wf.io.outputs

    def test_precheck_finalize_io(self) -> None:
        wf = precheck_finalize_workflow()
        assert wf.io is not None
        assert ".factory/reviews/adversarial-qa.md" in wf.io.inputs
        assert ".factory/experiments/verdict.json" in wf.io.outputs
        assert ".factory/archive/experiment.md" in wf.io.optional_outputs

    def test_io_chain_research_to_strategize(self) -> None:
        """research-pipeline outputs feed strategize inputs."""
        research = research_pipeline_workflow()
        strat = strategize_workflow()
        assert research.io is not None
        assert strat.io is not None
        assert strat.io.inputs.issubset(research.io.outputs)

    def test_io_chain_build_verify_to_precheck(self) -> None:
        """build-verify outputs feed precheck-finalize inputs."""
        bv = build_verify_workflow()
        pf = precheck_finalize_workflow()
        assert bv.io is not None
        assert pf.io is not None
        assert pf.io.inputs.issubset(bv.io.outputs)


# ── Node structure ────────────────────────────────────────────────


class TestSubWorkflowNodes:
    """Verify internal node structure matches expectations."""

    def test_research_pipeline_has_fork_join(self) -> None:
        wf = research_pipeline_workflow()
        assert isinstance(wf.nodes["fork_research"], ForkNode)
        assert isinstance(wf.nodes["join_research"], JoinNode)
        assert isinstance(wf.nodes["gate_research"], GateNode)
        assert len(wf.nodes["fork_research"].targets) == 3
        for name in ("researcher_similar", "researcher_techstack", "researcher_pitfalls"):
            assert isinstance(wf.nodes[name], AgentNode)
            assert wf.nodes[name].role == AgentRole.RESEARCHER

    def test_strategize_gate_type_agent(self) -> None:
        wf = strategize_workflow(gate_type="agent")
        gate = wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_strategize_gate_type_user(self) -> None:
        wf = strategize_workflow(gate_type="user")
        gate = wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_deep_qa_node_sequence(self) -> None:
        wf = deep_qa_workflow()
        assert "health_checker" in wf.nodes
        assert "code_reviewer" in wf.nodes
        assert "gate_review" in wf.nodes
        assert "adversarial_tester" in wf.nodes
        assert wf.start_node == "health_checker"

    def test_build_verify_has_builder_and_qa(self) -> None:
        wf = build_verify_workflow()
        assert isinstance(wf.nodes["builder"], AgentNode)
        assert wf.nodes["builder"].role == AgentRole.BUILDER
        assert "health_checker" in wf.nodes
        assert "code_reviewer" in wf.nodes
        assert "adversarial_tester" in wf.nodes
        assert "gate_qa" in wf.nodes
        assert "gate_doc_freshness" in wf.nodes

    def test_precheck_finalize_structure(self) -> None:
        wf = precheck_finalize_workflow()
        assert isinstance(wf.nodes["gate_precheck"], GateNode)
        assert isinstance(wf.nodes["finalize"], FnNode)
        assert isinstance(wf.nodes["archivist"], AgentNode)
        assert wf.nodes["archivist"].blocking is False

    def test_research_pipeline_name(self) -> None:
        wf = research_pipeline_workflow()
        assert wf.name == "research-pipeline"

    def test_strategize_name(self) -> None:
        wf = strategize_workflow()
        assert wf.name == "strategize"

    def test_deep_qa_name(self) -> None:
        wf = deep_qa_workflow()
        assert wf.name == "deep-qa"

    def test_build_verify_name(self) -> None:
        wf = build_verify_workflow()
        assert wf.name == "build-verify"

    def test_precheck_finalize_name(self) -> None:
        wf = precheck_finalize_workflow()
        assert wf.name == "precheck-finalize"


# ── Registration ──────────────────────────────────────────────────


class TestSubWorkflowRegistration:
    """Sub-workflows must be registered and discoverable."""

    def test_registered_in_builtin_registry(self) -> None:
        registry = _get_builtin_registry()
        for name in (
            "research-pipeline",
            "strategize",
            "deep-qa",
            "build-verify",
            "precheck-finalize",
        ):
            assert name in registry, f"'{name}' not in builtin registry"

    def test_register_all_includes_sub_workflows(self) -> None:
        all_workflows = register_all()
        for name in (
            "research-pipeline",
            "strategize",
            "deep-qa",
            "build-verify",
            "precheck-finalize",
        ):
            assert name in all_workflows, f"'{name}' not in register_all()"

    def test_registered_workflows_are_valid(self) -> None:
        all_workflows = register_all()
        for name in (
            "research-pipeline",
            "strategize",
            "deep-qa",
            "build-verify",
            "precheck-finalize",
        ):
            wf = all_workflows[name]
            issues = wf.validate_graph()
            assert issues == [], f"registered '{name}' has issues: {issues}"


# ── Existing workflow regression ──────────────────────────────────


class TestExistingWorkflowsStillValid:
    """Adding sub-workflows must not break existing monolithic workflows."""

    @pytest.mark.parametrize(
        "workflow_fn",
        [build_workflow, design_workflow, improve_workflow],
        ids=["build", "design", "improve"],
    )
    def test_existing_workflow_still_valid(self, workflow_fn):
        wf = workflow_fn()
        issues = wf.validate_graph()
        assert issues == [], f"{wf.name} has issues after sub-workflow additions: {issues}"

    def test_register_all_succeeds(self) -> None:
        all_workflows = register_all()
        assert len(all_workflows) > 0


# ── Dry-run execution ─────────────────────────────────────────────


class TestSubWorkflowDryRun:
    """Dry-run execution of each sub-workflow succeeds."""

    @pytest.fixture
    def project_path(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "strategy").mkdir(parents=True)
        (factory_dir / "reviews").mkdir(parents=True)
        (factory_dir / "experiments").mkdir(parents=True)
        (factory_dir / "archive").mkdir(parents=True)
        return tmp_path

    async def test_research_pipeline_dry_run(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = research_pipeline_workflow()
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None

    async def test_strategize_dry_run(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = strategize_workflow()
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None

    async def test_deep_qa_dry_run(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = deep_qa_workflow()
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None

    async def test_build_verify_dry_run(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = build_verify_workflow()
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None

    async def test_precheck_finalize_dry_run(self, project_path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = precheck_finalize_workflow()
        executor = WorkflowExecutor(wf, str(project_path), dry_run=True)
        result = await executor.execute()
        assert result is not None
