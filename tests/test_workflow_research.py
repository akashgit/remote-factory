"""Tests for research subgraph extraction and standalone research workflow."""

from __future__ import annotations

from factory.workflow.definitions import (
    ResearcherConfig,
    _get_builtin_registry,
    _research_subgraph,
    build_workflow,
    create_workflow,
    design_workflow,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
)


# ── _research_subgraph unit tests ─────────────────────────────────


class TestResearchSubgraph:
    def _build_configs(self, *, with_post_checks: bool) -> list[ResearcherConfig]:
        return [
            ResearcherConfig(
                id="alpha",
                prompt_template="Alpha prompt.",
                post_check_min_size=50 if with_post_checks else None,
            ),
            ResearcherConfig(
                id="beta",
                prompt_template="Beta prompt.",
                post_check_min_size=50 if with_post_checks else None,
            ),
            ResearcherConfig(
                id="gamma",
                prompt_template="Gamma prompt.",
                post_check_min_size=50 if with_post_checks else None,
            ),
        ]

    def test_returns_six_nodes(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        assert len(nodes) == 6

    def test_returns_seven_edges(self) -> None:
        _, edges = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        assert len(edges) == 7

    def test_node_ids(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        assert set(nodes.keys()) == {
            "fork_research",
            "researcher_alpha",
            "researcher_beta",
            "researcher_gamma",
            "join_research",
            "gate_research",
        }

    def test_fork_targets(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        fork = nodes["fork_research"]
        assert isinstance(fork, ForkNode)
        assert fork.targets == ["researcher_alpha", "researcher_beta", "researcher_gamma"]

    def test_researcher_roles(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        for rid in ("researcher_alpha", "researcher_beta", "researcher_gamma"):
            node = nodes[rid]
            assert isinstance(node, AgentNode)
            assert node.role == AgentRole.RESEARCHER

    def test_post_checks_present_when_min_size_set(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        node = nodes["researcher_alpha"]
        assert isinstance(node, AgentNode)
        assert len(node.post_checks) == 1
        assert node.post_checks[0].min_size == 50

    def test_post_checks_absent_when_min_size_none(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=False),
            gate_prompt="Gate prompt.",
        )
        node = nodes["researcher_alpha"]
        assert isinstance(node, AgentNode)
        assert len(node.post_checks) == 0

    def test_join_sources(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        join = nodes["join_research"]
        assert isinstance(join, JoinNode)
        assert join.sources == ["researcher_alpha", "researcher_beta", "researcher_gamma"]

    def test_gate_prompt(self) -> None:
        nodes, _ = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Custom gate prompt.",
        )
        gate = nodes["gate_research"]
        assert isinstance(gate, GateNode)
        assert gate.gate_prompt == "Custom gate prompt."

    def test_edge_structure(self) -> None:
        _, edges = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        edge_tuples = [(e.source, e.target, e.condition) for e in edges]
        assert ("fork_research", "researcher_alpha", None) in edge_tuples
        assert ("fork_research", "researcher_beta", None) in edge_tuples
        assert ("fork_research", "researcher_gamma", None) in edge_tuples
        assert ("researcher_alpha", "join_research", None) in edge_tuples
        assert ("researcher_beta", "join_research", None) in edge_tuples
        assert ("researcher_gamma", "join_research", None) in edge_tuples
        assert ("join_research", "gate_research", None) in edge_tuples

    def test_no_exit_edges(self) -> None:
        _, edges = _research_subgraph(
            researchers=self._build_configs(with_post_checks=True),
            gate_prompt="Gate prompt.",
        )
        exit_edges = [
            e for e in edges
            if e.source == "gate_research"
            and e.condition in (VerdictType.PROCEED, VerdictType.RELOOP)
        ]
        assert exit_edges == []


# ── Workflow node/edge preservation after refactor ────────────────


class TestBuildWorkflowPreservation:
    def test_research_node_ids(self) -> None:
        wf = build_workflow()
        expected = {
            "fork_research", "researcher_similar", "researcher_techstack",
            "researcher_pitfalls", "join_research", "gate_research",
        }
        assert expected.issubset(set(wf.nodes.keys()))

    def test_research_edge_tuples(self) -> None:
        wf = build_workflow()
        edge_tuples = {(e.source, e.target, e.condition) for e in wf.edges}
        assert ("fork_research", "researcher_similar", None) in edge_tuples
        assert ("fork_research", "researcher_techstack", None) in edge_tuples
        assert ("fork_research", "researcher_pitfalls", None) in edge_tuples
        assert ("researcher_similar", "join_research", None) in edge_tuples
        assert ("researcher_techstack", "join_research", None) in edge_tuples
        assert ("researcher_pitfalls", "join_research", None) in edge_tuples
        assert ("join_research", "gate_research", None) in edge_tuples
        assert ("gate_research", "strategist", VerdictType.PROCEED) in edge_tuples
        assert ("gate_research", "fork_research", VerdictType.RELOOP) in edge_tuples

    def test_post_checks_present(self) -> None:
        wf = build_workflow()
        for rid in ("researcher_similar", "researcher_techstack", "researcher_pitfalls"):
            node = wf.nodes[rid]
            assert isinstance(node, AgentNode)
            assert len(node.post_checks) == 1
            assert node.post_checks[0].min_size == 50

    def test_validates(self) -> None:
        wf = build_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"build_workflow graph issues: {issues}"


class TestCreateWorkflowPreservation:
    def test_research_node_ids(self) -> None:
        wf = create_workflow()
        expected = {
            "fork_research", "researcher_existing", "researcher_intent",
            "researcher_practices", "join_research", "gate_research",
        }
        assert expected.issubset(set(wf.nodes.keys()))

    def test_research_edge_tuples(self) -> None:
        wf = create_workflow()
        edge_tuples = {(e.source, e.target, e.condition) for e in wf.edges}
        assert ("fork_research", "researcher_existing", None) in edge_tuples
        assert ("fork_research", "researcher_intent", None) in edge_tuples
        assert ("fork_research", "researcher_practices", None) in edge_tuples
        assert ("researcher_existing", "join_research", None) in edge_tuples
        assert ("researcher_intent", "join_research", None) in edge_tuples
        assert ("researcher_practices", "join_research", None) in edge_tuples
        assert ("join_research", "gate_research", None) in edge_tuples
        assert ("gate_research", "strategist", VerdictType.PROCEED) in edge_tuples
        assert ("gate_research", "fork_research", VerdictType.RELOOP) in edge_tuples

    def test_no_post_checks(self) -> None:
        wf = create_workflow()
        for rid in ("researcher_existing", "researcher_intent", "researcher_practices"):
            node = wf.nodes[rid]
            assert isinstance(node, AgentNode)
            assert len(node.post_checks) == 0

    def test_validates(self) -> None:
        wf = create_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"create_workflow graph issues: {issues}"


class TestDesignWorkflowPreservation:
    def test_inherits_build_research_nodes(self) -> None:
        wf = design_workflow()
        expected = {
            "fork_research", "researcher_similar", "researcher_techstack",
            "researcher_pitfalls", "join_research", "gate_research",
        }
        assert expected.issubset(set(wf.nodes.keys()))

    def test_research_edge_tuples(self) -> None:
        wf = design_workflow()
        edge_tuples = {(e.source, e.target, e.condition) for e in wf.edges}
        assert ("fork_research", "researcher_similar", None) in edge_tuples
        assert ("researcher_similar", "join_research", None) in edge_tuples
        assert ("join_research", "gate_research", None) in edge_tuples

    def test_validates(self) -> None:
        wf = design_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"design_workflow graph issues: {issues}"


# ── Standalone research workflow ──────────────────────────────────


class TestResearchStandaloneWorkflow:
    def _get_wf(self):
        from factory.workflow.research import workflow
        return workflow()

    def test_valid_graph(self) -> None:
        wf = self._get_wf()
        issues = wf.validate_graph()
        assert issues == [], f"research-standalone workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = self._get_wf()
        assert wf.name == "research-standalone"

    def test_start_node(self) -> None:
        wf = self._get_wf()
        assert wf.start_node == "fork_research"

    def test_has_expected_nodes(self) -> None:
        wf = self._get_wf()
        assert set(wf.nodes.keys()) == {
            "fork_research",
            "researcher_similar",
            "researcher_techstack",
            "researcher_pitfalls",
            "join_research",
            "gate_research",
        }

    def test_specialist_reads_cleared(self) -> None:
        wf = self._get_wf()
        for nid in ("researcher_similar", "researcher_techstack", "researcher_pitfalls"):
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.reads == set()

    def test_trigger_fires_for_research_standalone(self) -> None:
        from factory.models import ProjectState
        wf = self._get_wf()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "research-standalone"})

    def test_trigger_does_not_fire_for_other_modes(self) -> None:
        from factory.models import ProjectState
        wf = self._get_wf()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "research"})

    def test_not_registered_after_mode_removal(self) -> None:
        reg = _get_builtin_registry()
        assert "research-standalone" not in reg
