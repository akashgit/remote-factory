"""Tests for the design-v2 workflow — inference-time scaling with dynamic research/strategy/QA."""

from __future__ import annotations

import pytest

from factory.workflow.contributed.design_v2 import meta as design_v2_meta
from factory.workflow.contributed.design_v2 import workflow as design_v2_workflow
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
)


@pytest.fixture(scope="module")
def design_v2_module():
    class _Module:
        meta = design_v2_meta
        workflow = staticmethod(design_v2_workflow)
    return _Module()


@pytest.fixture(scope="module")
def design_v2_wf():
    return design_v2_workflow()


# ── Module-level metadata ──────────────────────────────────────


class TestMeta:
    def test_meta_has_name(self, design_v2_module) -> None:
        assert "name" in design_v2_module.meta
        assert design_v2_module.meta["name"] == "design-v2"

    def test_meta_has_description(self, design_v2_module) -> None:
        assert "description" in design_v2_module.meta
        assert len(design_v2_module.meta["description"]) > 0


# ── Graph structure ────────────────────────────────────────────


class TestGraphStructure:
    def test_node_count(self, design_v2_wf) -> None:
        assert len(design_v2_wf.nodes) == 31

    def test_edge_count(self, design_v2_wf) -> None:
        assert len(design_v2_wf.edges) == 35

    def test_workflow_name(self, design_v2_wf) -> None:
        assert design_v2_wf.name == "design-v2"

    def test_start_node(self, design_v2_wf) -> None:
        assert design_v2_wf.start_node == "init_user_intent"

    def test_terminal(self, design_v2_wf) -> None:
        assert design_v2_wf.terminal is True

    def test_validates(self, design_v2_wf) -> None:
        issues = design_v2_wf.validate_graph()
        assert issues == [], f"design-v2 workflow has issues: {issues}"


# ── Key nodes present ──────────────────────────────────────────


class TestKeyNodesPresent:
    @pytest.mark.parametrize(
        "node_id",
        [
            "init_user_intent",
            "research_director",
            "strategy_director",
            "synthesize_strategy",
            "design_doc",
            "qa_director",
            "synthesize_qa",
            "gate_strategy",
            "gate_qa",
            "fork_qa",
            "join_qa",
            "health_checker",
            "code_reviewer",
            "builder",
            "gate_has_factory",
            "discover",
            "graph_update",
            "study",
            "graph_explorer",
            "concat_study",
            "overwatch",
            "gate_overwatch",
        ],
    )
    def test_node_exists(self, design_v2_wf, node_id: str) -> None:
        assert node_id in design_v2_wf.nodes, f"missing node: {node_id}"


# ── Removed nodes absent ──────────────────────────────────────


class TestRemovedNodesAbsent:
    @pytest.mark.parametrize(
        "node_id",
        [
            "fork_research",
            "researcher_similar",
            "researcher_techstack",
            "researcher_pitfalls",
            "join_research",
            "gate_research",
            "strategist",
            "adversarial_tester",
        ],
    )
    def test_node_removed(self, design_v2_wf, node_id: str) -> None:
        assert node_id not in design_v2_wf.nodes, f"node should be removed: {node_id}"


# ── Node types and properties ─────────────────────────────────


class TestNodeProperties:
    def test_gate_strategy_is_user(self, design_v2_wf) -> None:
        gate = design_v2_wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_gate_qa_is_agent(self, design_v2_wf) -> None:
        gate = design_v2_wf.nodes["gate_qa"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_research_director_is_ceo(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["research_director"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 3600

    def test_strategy_director_is_ceo(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["strategy_director"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 3600

    def test_qa_director_is_ceo(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["qa_director"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 3600

    def test_synthesize_strategy_is_strategist(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["synthesize_strategy"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_design_doc_is_strategist(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["design_doc"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_synthesize_qa_is_fn_node(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["synthesize_qa"]
        assert isinstance(node, FnNode)
        assert ".factory/reviews/qa-synthesized.md" in node.writes

    def test_init_user_intent_is_fn_node(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["init_user_intent"]
        assert isinstance(node, FnNode)
        assert ".factory/strategy/user-intent.md" in node.writes

    def test_overwatch_is_ceo(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["overwatch"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 1800

    def test_gate_overwatch_is_agent(self, design_v2_wf) -> None:
        gate = design_v2_wf.nodes["gate_overwatch"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_fork_qa_targets(self, design_v2_wf) -> None:
        fork = design_v2_wf.nodes["fork_qa"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {"health_checker", "code_reviewer", "qa_director"}

    def test_join_qa_sources(self, design_v2_wf) -> None:
        join = design_v2_wf.nodes["join_qa"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == {"health_checker", "code_reviewer", "qa_director"}


# ── Edge wiring ────────────────────────────────────────────────


class TestEdgeWiring:
    def _edge_set(self, wf):
        return {(e.source, e.target, e.condition) for e in wf.edges}

    def test_concat_study_to_research_director(self, design_v2_wf) -> None:
        assert ("concat_study", "research_director", None) in self._edge_set(design_v2_wf)

    def test_research_director_to_strategy_director(self, design_v2_wf) -> None:
        assert ("research_director", "strategy_director", None) in self._edge_set(design_v2_wf)

    def test_strategy_director_to_synthesize_strategy(self, design_v2_wf) -> None:
        assert ("strategy_director", "synthesize_strategy", None) in self._edge_set(design_v2_wf)

    def test_synthesize_strategy_to_design_doc(self, design_v2_wf) -> None:
        assert ("synthesize_strategy", "design_doc", None) in self._edge_set(design_v2_wf)

    def test_design_doc_to_gate_strategy(self, design_v2_wf) -> None:
        assert ("design_doc", "gate_strategy", None) in self._edge_set(design_v2_wf)

    def test_gate_strategy_reloop_to_strategy_director(self, design_v2_wf) -> None:
        assert (
            "gate_strategy",
            "strategy_director",
            VerdictType.RELOOP,
        ) in self._edge_set(design_v2_wf)

    def test_join_qa_to_synthesize_qa(self, design_v2_wf) -> None:
        assert ("join_qa", "synthesize_qa", None) in self._edge_set(design_v2_wf)

    def test_synthesize_qa_to_gate_qa(self, design_v2_wf) -> None:
        assert ("synthesize_qa", "gate_qa", None) in self._edge_set(design_v2_wf)

    def test_init_user_intent_to_gate_has_factory(self, design_v2_wf) -> None:
        assert ("init_user_intent", "gate_has_factory", None) in self._edge_set(design_v2_wf)

    def test_gate_has_factory_routes(self, design_v2_wf) -> None:
        edges = self._edge_set(design_v2_wf)
        assert ("gate_has_factory", "graph_update", VerdictType.PROCEED) in edges
        assert ("gate_has_factory", "discover", VerdictType.HALT) in edges

    def test_gate_qa_proceed_to_overwatch(self, design_v2_wf) -> None:
        assert (
            "gate_qa",
            "overwatch",
            VerdictType.PROCEED,
        ) in self._edge_set(design_v2_wf)

    def test_overwatch_to_gate_overwatch(self, design_v2_wf) -> None:
        assert ("overwatch", "gate_overwatch", None) in self._edge_set(design_v2_wf)

    def test_gate_overwatch_proceed_to_doc_freshness(self, design_v2_wf) -> None:
        assert (
            "gate_overwatch",
            "gate_doc_freshness",
            VerdictType.PROCEED,
        ) in self._edge_set(design_v2_wf)

    def test_gate_overwatch_reloop_to_builder(self, design_v2_wf) -> None:
        assert (
            "gate_overwatch",
            "builder",
            VerdictType.RELOOP,
        ) in self._edge_set(design_v2_wf)

    def test_no_old_gate_qa_to_doc_freshness_edge(self, design_v2_wf) -> None:
        direct = [
            e
            for e in design_v2_wf.edges
            if e.source == "gate_qa" and e.target == "gate_doc_freshness"
        ]
        assert direct == [], "old gate_qa -> gate_doc_freshness edge should be removed"

    def test_no_old_join_qa_to_gate_qa_edge(self, design_v2_wf) -> None:
        """The old direct join_qa -> gate_qa edge must be replaced by join_qa -> synthesize_qa."""
        direct = [
            e
            for e in design_v2_wf.edges
            if e.source == "join_qa" and e.target == "gate_qa"
        ]
        assert direct == [], "old direct join_qa -> gate_qa edge should be removed"


# ── Post checks on director nodes ─────────────────────────────


class TestPostChecks:
    def test_research_director_post_check(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["research_director"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/strategy/research-plan.json"

    def test_strategy_director_post_check(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["strategy_director"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/strategy/strategy-plan.json"

    def test_synthesize_strategy_post_check(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["synthesize_strategy"]
        checks = node.post_checks
        assert len(checks) == 1
        assert checks[0].path == ".factory/strategy/current.md"
        assert checks[0].min_size == 200

    def test_design_doc_post_check(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["design_doc"]
        checks = node.post_checks
        assert len(checks) == 1
        assert checks[0].path == ".factory/strategy/current.md"
        assert checks[0].min_size == 500
        assert "## What We're Building" in checks[0].must_contain
        assert "## Architecture" in checks[0].must_contain

    def test_overwatch_post_check(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["overwatch"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/reviews/overwatch-latest.md"
        assert node.post_checks[0].min_size == 100

    def test_qa_director_post_check(self, design_v2_wf) -> None:
        node = design_v2_wf.nodes["qa_director"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/reviews/qa-plan.json"
