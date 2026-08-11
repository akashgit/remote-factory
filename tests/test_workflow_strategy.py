"""Tests for strategy subgraph extraction and standalone strategy workflow."""

from __future__ import annotations

import pytest

from factory.workflow.definitions import (
    StrategyConfig,
    _get_builtin_registry,
    _strategy_subgraph,
    build_workflow,
    improve_workflow,
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


# ── _strategy_subgraph unit tests ───────────────────────────────


class TestStrategySubgraph:
    def _config(self) -> StrategyConfig:
        return StrategyConfig(
            prompt_template="Generate a plan.",
            reads=frozenset({".factory/strategy/research-combined.md"}),
            post_checks=(
                ArtifactCheck(
                    path=".factory/strategy/current.md",
                    must_exist=True,
                    min_size=200,
                    must_contain=["### Phase 1", "### Architecture"],
                ),
            ),
            gate_prompt="HARD GATE — check depth and buildability.",
        )

    def test_returns_two_nodes(self) -> None:
        nodes, _ = _strategy_subgraph(config=self._config())
        assert set(nodes.keys()) == {"strategist", "gate_strategy"}

    def test_returns_one_internal_edge(self) -> None:
        _, edges = _strategy_subgraph(config=self._config())
        assert edges == [Edge(source="strategist", target="gate_strategy")]

    def test_strategist_contract(self) -> None:
        nodes, _ = _strategy_subgraph(config=self._config())
        node = nodes["strategist"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST
        assert node.writes == {".factory/strategy/current.md"}
        assert len(node.post_checks) == 1
        assert node.post_checks[0].min_size == 200

    def test_gate_is_agent_evaluated_by_default(self) -> None:
        nodes, _ = _strategy_subgraph(config=self._config())
        gate = nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_gate_can_be_user_evaluated(self) -> None:
        config = self._config()
        nodes, _ = _strategy_subgraph(
            config=config.__class__(
                id=config.id,
                prompt_template=config.prompt_template,
                reads=config.reads,
                post_checks=config.post_checks,
                gate_prompt=config.gate_prompt,
                gate_evaluator_type="user",
            )
        )
        gate = nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"
        assert gate.evaluator_role is None


# ── Preservation: build/improve graphs unchanged ────────────────


class TestStrategyPreservation:
    def test_build_workflow_has_expected_strategy_nodes(self) -> None:
        wf = build_workflow()
        assert set(wf.nodes.keys()) >= {"strategist", "gate_strategy"}
        assert (
            Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED)
            in wf.edges
        )
        assert (
            Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP)
            in wf.edges
        )

    def test_improve_workflow_has_expected_strategy_nodes(self) -> None:
        wf = improve_workflow()
        assert set(wf.nodes.keys()) >= {"strategist", "gate_strategy"}
        assert (
            Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP)
            in wf.edges
        )

    def test_build_improve_strategy_variants_differ(self) -> None:
        build = build_workflow().nodes["strategist"]
        improve = improve_workflow().nodes["strategist"]
        assert isinstance(build, AgentNode) and isinstance(improve, AgentNode)
        assert build.prompt_template != improve.prompt_template
        assert build.reads != improve.reads


# ── Standalone workflow ─────────────────────────────────────────


class TestStrategyStandaloneWorkflow:
    def _get_wf(self):
        from factory.workflow.strategy import workflow

        return workflow()

    def test_valid_graph(self) -> None:
        wf = self._get_wf()
        issues = wf.validate_graph()
        assert issues == [], f"strategy-standalone workflow has issues: {issues}"

    def test_name(self) -> None:
        assert self._get_wf().name == "strategy-standalone"

    def test_start_node(self) -> None:
        assert self._get_wf().start_node == "strategist"

    def test_has_expected_nodes(self) -> None:
        wf = self._get_wf()
        assert set(wf.nodes.keys()) == {"strategist", "gate_strategy"}

    def test_specialist_reads_cleared(self) -> None:
        wf = self._get_wf()
        node = wf.nodes["strategist"]
        assert isinstance(node, AgentNode)
        assert node.reads == set()

    def test_reloop_edge_returns_to_strategist(self) -> None:
        wf = self._get_wf()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_strategy" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "strategist"

    def test_trigger_fires_for_strategy_standalone(self) -> None:
        from factory.models import ProjectState

        wf = self._get_wf()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "strategy-standalone"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})

    def test_registered(self) -> None:
        reg = _get_builtin_registry()
        assert "strategy-standalone" in reg

    def test_register_all_includes_it(self) -> None:
        all_wf = register_all()
        assert "strategy-standalone" in all_wf

    def test_meta_exported(self) -> None:
        from factory.workflow.skill_export import WORKFLOW_META

        assert "strategy-standalone" in WORKFLOW_META
        assert WORKFLOW_META["strategy-standalone"]["description"]

    @pytest.mark.parametrize("name", ["build", "improve", "design"])
    def test_parent_graphs_still_validate(self, name: str) -> None:
        all_wf = register_all()
        assert all_wf[name].validate_graph() == []
