"""Tests for W₁₀: Benchmark Mode workflow."""

from factory.models import ProjectState
from factory.workflow.definitions import benchmark_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    Study,
    VerdictType,
)
from factory.workflow.skill_export import validate_skill, workflow_to_skill_md


class TestBenchmarkWorkflowGraph:
    """Test benchmark_workflow() graph structure."""

    def test_graph_validates(self):
        """Graph passes structural validation (no dangling edges, reachable nodes)."""
        wf = benchmark_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Graph validation issues: {issues}"

    def test_node_count(self):
        """Benchmark has 14 nodes."""
        wf = benchmark_workflow()
        assert len(wf.nodes) == 14

    def test_edge_count(self):
        """Benchmark has 17 edges."""
        wf = benchmark_workflow()
        assert len(wf.edges) == 17

    def test_start_node(self):
        wf = benchmark_workflow()
        assert wf.start_node == "study"
        assert "study" in wf.nodes

    def test_study_node_type(self):
        wf = benchmark_workflow()
        assert isinstance(wf.nodes["study"], Study)
        assert wf.nodes["study"].command == "factory study {project_path}"

    def test_researcher_node(self):
        wf = benchmark_workflow()
        node = wf.nodes["researcher"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert ".factory/strategy/observations.md" in node.reads
        assert ".factory/strategy/research-local.md" in node.writes

    def test_auto_merge_node(self):
        """auto_merge is a FnNode that merges experiment branch to base."""
        wf = benchmark_workflow()
        node = wf.nodes["auto_merge"]
        assert isinstance(node, FnNode)
        assert "git merge" in node.command
        assert "git checkout" in node.command
        assert ".factory/experiments/verdict.json" in node.reads

    def test_archivist_is_non_blocking(self):
        wf = benchmark_workflow()
        node = wf.nodes["archivist"]
        assert isinstance(node, AgentNode)
        assert node.blocking is False

    def test_builder_no_pr_in_prompt(self):
        """Builder prompt must NOT mention creating PRs (Sacred Rule 6 suspended)."""
        wf = benchmark_workflow()
        node = wf.nodes["builder"]
        prompt = node.prompt_template.lower()
        assert "do not create a pr" in prompt or "do not" in prompt

    def test_gate_qa_reloop_to_builder(self):
        """gate_qa has a RELOOP edge back to builder."""
        wf = benchmark_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_qa" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "builder"

    def test_finalize_to_auto_merge_edge(self):
        """finalize connects to auto_merge."""
        wf = benchmark_workflow()
        edges = [e for e in wf.edges if e.source == "finalize" and e.target == "auto_merge"]
        assert len(edges) == 1

    def test_auto_merge_to_archivist_edge(self):
        """auto_merge connects to archivist."""
        wf = benchmark_workflow()
        edges = [e for e in wf.edges if e.source == "auto_merge" and e.target == "archivist"]
        assert len(edges) == 1

    def test_auto_merge_fallback(self):
        """auto_merge command has fallback for missing remote HEAD."""
        wf = benchmark_workflow()
        node = wf.nodes["auto_merge"]
        assert "|| echo main" in node.command


class TestBenchmarkTrigger:
    """Test the benchmark trigger function."""

    def test_trigger_on_benchmark_mode(self):
        wf = benchmark_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "benchmark"}) is True

    def test_trigger_rejects_improve_mode(self):
        wf = benchmark_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False

    def test_trigger_rejects_no_mode(self):
        wf = benchmark_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False

    def test_trigger_with_any_project_state(self):
        """Benchmark trigger ignores project state — only checks mode."""
        wf = benchmark_workflow()
        assert wf.trigger is not None
        for state in ProjectState:
            result = wf.trigger(state, {"mode": "benchmark"})
            assert result is True, f"Trigger should return True for state={state}"


class TestBenchmarkSkillExport:
    """Test SKILL.md generation for benchmark workflow."""

    def test_skill_md_generates(self):
        wf = benchmark_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert skill_md.startswith("---")
        assert "workflow-benchmark" in skill_md

    def test_skill_md_validates(self):
        wf = benchmark_workflow()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"SKILL.md validation issues: {issues}"

    def test_skill_md_contains_auto_merge(self):
        wf = benchmark_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert "auto_merge" in skill_md.lower() or "auto merge" in skill_md.lower()


class TestBenchmarkRegistration:
    """Test benchmark workflow is registered in register_all()."""

    def test_registered(self):
        workflows = register_all()
        assert "benchmark" in workflows

    def test_is_workflow_type(self):
        from factory.workflow.primitives import Workflow
        workflows = register_all()
        assert isinstance(workflows["benchmark"], Workflow)

    def test_workflow_count(self):
        """Factory should have 10 workflows after adding benchmark."""
        workflows = register_all()
        assert len(workflows) == 10
