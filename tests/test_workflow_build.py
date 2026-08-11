"""Tests for build subgraph extraction and standalone build workflow."""

from __future__ import annotations

import pytest

from factory.workflow.definitions import (
    BuildConfig,
    _build_subgraph,
    _get_builtin_registry,
    build_workflow,
    design_workflow,
    register_all,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    GateNode,
    VerdictType,
)


# ── _build_subgraph unit tests ──────────────────────────────────


class TestBuildSubgraph:
    def _config(self) -> BuildConfig:
        return BuildConfig(
            prompt_template="Implement the phase.",
            reads=frozenset({".factory/strategy/current.md"}),
            post_checks=(
                ArtifactCheck(
                    path=".factory/reviews/builder-latest.md",
                    must_exist=True,
                    min_size=500,
                    must_contain=["commit"],
                ),
            ),
            gate_prompt="Does the work match the plan?",
        )

    def test_returns_two_nodes(self) -> None:
        nodes, _ = _build_subgraph(config=self._config())
        assert set(nodes.keys()) == {"builder", "gate_build"}

    def test_returns_one_internal_edge(self) -> None:
        _, edges = _build_subgraph(config=self._config())
        assert edges == [Edge(source="builder", target="gate_build")]

    def test_builder_contract(self) -> None:
        nodes, _ = _build_subgraph(config=self._config())
        node = nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.writes == {".factory/reviews/builder-latest.md"}
        assert len(node.post_checks) == 1
        assert node.post_checks[0].min_size == 500

    def test_gate_contract(self) -> None:
        nodes, _ = _build_subgraph(config=self._config())
        gate = nodes["gate_build"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO
        assert gate.reads == {".factory/reviews/builder-latest.md"}


# ── Preservation: parent graphs unchanged ───────────────────────


class TestBuildPreservation:
    def test_build_workflow_has_expected_build_nodes(self) -> None:
        wf = build_workflow()
        assert set(wf.nodes.keys()) >= {"builder", "gate_build"}
        assert (
            Edge(
                source="gate_build",
                target="health_checker",
                condition=VerdictType.PROCEED,
            )
            in wf.edges
        )
        assert (
            Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP)
            in wf.edges
        )

    def test_design_workflow_inherits_build_stage(self) -> None:
        wf = design_workflow()
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert builder.role == AgentRole.BUILDER
        assert (
            Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED)
            in wf.edges
        )


# ── Standalone workflow ─────────────────────────────────────────


class TestBuildStandaloneWorkflow:
    def _get_wf(self):
        from factory.workflow.build import workflow

        return workflow()

    def test_valid_graph(self) -> None:
        wf = self._get_wf()
        issues = wf.validate_graph()
        assert issues == [], f"build-standalone workflow has issues: {issues}"

    def test_name(self) -> None:
        assert self._get_wf().name == "build-standalone"

    def test_start_node(self) -> None:
        assert self._get_wf().start_node == "builder"

    def test_has_expected_nodes(self) -> None:
        wf = self._get_wf()
        assert set(wf.nodes.keys()) == {
            "builder",
            "gate_build",
            "health_checker",
            "code_reviewer",
            "gate_review",
            "adversarial_tester",
            "gate_qa",
        }

    def test_includes_deep_qa_eval_loop(self) -> None:
        """A build factory must have its own eval (deep-QA reachable from builder)."""
        wf = self._get_wf()
        qa_roles = {AgentRole.HEALTH_CHECKER, AgentRole.CODE_REVIEWER, AgentRole.ADVERSARIAL_TESTER}
        qa_nodes = [
            nid
            for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role in qa_roles
        ]
        assert set(qa_nodes) == {"health_checker", "code_reviewer", "adversarial_tester"}
        assert (
            Edge(
                source="gate_build",
                target="health_checker",
                condition=VerdictType.PROCEED,
            )
            in wf.edges
        )
        assert (
            Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP)
            in wf.edges
        )

    def test_specialist_reads_cleared(self) -> None:
        wf = self._get_wf()
        for nid in ("builder", "health_checker", "code_reviewer", "adversarial_tester"):
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.reads == set()

    def test_trigger_fires_for_build_standalone(self) -> None:
        from factory.models import ProjectState

        wf = self._get_wf()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "build-standalone"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})

    def test_registered(self) -> None:
        reg = _get_builtin_registry()
        assert "build-standalone" in reg

    def test_register_all_includes_it(self) -> None:
        all_wf = register_all()
        assert "build-standalone" in all_wf

    def test_meta_exported(self) -> None:
        from factory.workflow.skill_export import WORKFLOW_META

        assert "build-standalone" in WORKFLOW_META
        assert WORKFLOW_META["build-standalone"]["description"]

    @pytest.mark.parametrize("name", ["build", "design"])
    def test_parent_graphs_still_validate(self, name: str) -> None:
        all_wf = register_all()
        assert all_wf[name].validate_graph() == []
