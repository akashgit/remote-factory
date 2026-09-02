"""Tests for Package-based mode composition."""

from __future__ import annotations

from factory.workflow.packages import (
    BUILD_RESEARCHERS,
    build_mode,
    build_package,
    design_mode,
    design_with_frontend_mode,
    discovery_package,
    qa_package,
    research_package,
    strategy_package,
    study_package,
)
from factory.workflow.primitives import AgentNode, ForkNode, GateNode, JoinNode


class TestStudyPackage:
    def test_compiles(self):
        pkg = study_package()
        wf = pkg.compile()
        assert len(wf.nodes) == 4

    def test_has_expected_nodes(self):
        wf = study_package().compile()
        assert "graph_update" in wf.nodes
        assert "study" in wf.nodes
        assert "graph_explorer" in wf.nodes
        assert "concat_study" in wf.nodes

    def test_focus_propagates(self):
        wf = study_package(focus="auth").compile()
        node = wf.nodes["graph_explorer"]
        assert isinstance(node, AgentNode)
        assert "auth" in node.prompt_template

    def test_produces_study_complete(self):
        pkg = study_package()
        assert "study_complete" in pkg.contract.produces


class TestResearchPackage:
    def test_compiles(self):
        pkg = research_package(
            researchers=BUILD_RESEARCHERS,
            gate_prompt="Check research quality.",
        )
        wf = pkg.compile()
        assert len(wf.nodes) >= 5  # fork + 3 researchers + join + gate

    def test_has_fork_join(self):
        wf = research_package(
            researchers=BUILD_RESEARCHERS,
            gate_prompt="Check.",
        ).compile()
        assert "fork_research" in wf.nodes
        assert "join_research" in wf.nodes
        assert isinstance(wf.nodes["fork_research"], ForkNode)
        assert isinstance(wf.nodes["join_research"], JoinNode)

    def test_gate_has_reloop(self):
        wf = research_package(
            researchers=BUILD_RESEARCHERS,
            gate_prompt="Check.",
        ).compile()
        reloop_edges = [e for e in wf.edges if e.source == "gate_research" and e.target == "fork_research"]
        assert len(reloop_edges) == 1


class TestStrategyPackage:
    def test_compiles(self):
        wf = strategy_package().compile()
        assert "strategist" in wf.nodes
        assert "gate_strategy" in wf.nodes

    def test_gate_has_reloop_to_strategist(self):
        wf = strategy_package().compile()
        reloop = [e for e in wf.edges if e.source == "gate_strategy" and e.target == "strategist"]
        assert len(reloop) == 1


class TestBuildPackage:
    def test_compiles(self):
        wf = build_package().compile()
        assert "builder" in wf.nodes
        assert "gate_build" in wf.nodes
        assert len(wf.nodes) == 2


class TestQaPackage:
    def test_compiles(self):
        wf = qa_package().compile()
        assert len(wf.nodes) == 9

    def test_parallel_qa_agents(self):
        wf = qa_package().compile()
        assert "health_checker" in wf.nodes
        assert "code_reviewer" in wf.nodes
        assert "adversarial_tester" in wf.nodes

    def test_has_gates(self):
        wf = qa_package().compile()
        assert isinstance(wf.nodes["gate_qa"], GateNode)
        assert isinstance(wf.nodes["gate_precheck"], GateNode)


class TestBuildMode:
    def test_compiles(self):
        wf = build_mode().compile()
        assert len(wf.nodes) >= 20

    def test_has_all_stages(self):
        wf = build_mode().compile()
        assert "study" in wf.nodes
        assert "fork_research" in wf.nodes
        assert "strategist" in wf.nodes
        assert "builder" in wf.nodes
        assert "fork_qa" in wf.nodes

    def test_focus_reaches_study(self):
        wf = build_mode(focus="payments").compile()
        node = wf.nodes["graph_explorer"]
        assert isinstance(node, AgentNode)
        assert "payments" in node.prompt_template

    def test_node_count_matches_expectation(self):
        wf = build_mode().compile()
        assert len(wf.nodes) == 24
        assert len(wf.edges) == 30


class TestDiscoveryPackage:
    def test_compiles(self):
        wf = discovery_package().compile()
        assert "gate_has_factory" in wf.nodes
        assert "discover" in wf.nodes

    def test_has_bootstrap_path(self):
        wf = discovery_package().compile()
        assert "create_factory_md" in wf.nodes
        assert "factory_init" in wf.nodes

    def test_has_skip_path(self):
        wf = discovery_package().compile()
        assert "skip_bootstrap" in wf.nodes


class TestDesignMode:
    def test_compiles(self):
        wf = design_mode().compile()
        assert len(wf.nodes) == 30

    def test_has_discovery_and_study(self):
        wf = design_mode().compile()
        assert "gate_has_factory" in wf.nodes
        assert "study" in wf.nodes
        assert "strategist" in wf.nodes
        assert "builder" in wf.nodes

    def test_superset_of_build(self):
        build_nodes = set(build_mode().compile().nodes.keys())
        design_nodes = set(design_mode().compile().nodes.keys())
        assert build_nodes.issubset(design_nodes)


class TestDesignWithFrontend:
    def test_compiles(self):
        wf = design_with_frontend_mode().compile()
        assert len(wf.nodes) == 31

    def test_has_frontend_discovery(self):
        wf = design_with_frontend_mode().compile()
        assert "frontend_discovery" in wf.nodes

    def test_one_node_more_than_design(self):
        design_nodes = set(design_mode().compile().nodes.keys())
        frontend_nodes = set(design_with_frontend_mode().compile().nodes.keys())
        extra = frontend_nodes - design_nodes
        assert extra == {"frontend_discovery"}

    def test_frontend_reads_study(self):
        wf = design_with_frontend_mode().compile()
        node = wf.nodes["frontend_discovery"]
        assert isinstance(node, AgentNode)
        assert ".factory/strategy/study-combined.md" in node.reads
