"""Tests for the spike-sort workflow definition."""

from __future__ import annotations

from factory.workflow.definitions import spike_sort_workflow
from factory.workflow.primitives import AgentNode, AgentRole, FnNode


class TestSpikeSortWorkflow:
    def test_spike_sort_valid(self) -> None:
        wf = spike_sort_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"spike-sort workflow has issues: {issues}"

    def test_spike_sort_has_10_nodes(self) -> None:
        wf = spike_sort_workflow()
        fn_nodes = [n for n in wf.nodes.values() if isinstance(n, FnNode)]
        agent_nodes = [n for n in wf.nodes.values() if isinstance(n, AgentNode)]
        assert len(fn_nodes) == 7
        assert len(agent_nodes) == 3
        assert len(wf.nodes) == 10

    def test_spike_sort_node_ids(self) -> None:
        wf = spike_sort_workflow()
        expected = {
            "preprocess",
            "detect_trial",
            "detect_params",
            "detect",
            "localize",
            "cluster_params",
            "cluster",
            "templates",
            "match",
            "qa_sorting",
        }
        assert set(wf.nodes.keys()) == expected

    def test_spike_sort_is_linear(self) -> None:
        wf = spike_sort_workflow()
        expected_chain = [
            ("preprocess", "detect_trial"),
            ("detect_trial", "detect_params"),
            ("detect_params", "detect"),
            ("detect", "localize"),
            ("localize", "cluster_params"),
            ("cluster_params", "cluster"),
            ("cluster", "templates"),
            ("templates", "match"),
            ("match", "qa_sorting"),
        ]
        assert len(wf.edges) == 9
        actual = [(e.source, e.target) for e in wf.edges]
        assert actual == expected_chain

    def test_spike_sort_agent_models(self) -> None:
        wf = spike_sort_workflow()
        dp = wf.nodes["detect_params"]
        assert isinstance(dp, AgentNode)
        assert dp.role == AgentRole.RESEARCHER
        assert dp.model == "haiku"
        assert dp.timeout == 60

        cp = wf.nodes["cluster_params"]
        assert isinstance(cp, AgentNode)
        assert cp.role == AgentRole.STRATEGIST
        assert cp.model == "sonnet"
        assert cp.timeout == 120

        qa = wf.nodes["qa_sorting"]
        assert isinstance(qa, AgentNode)
        assert qa.role == AgentRole.HEALTH_CHECKER
        assert qa.model == "sonnet"
        assert qa.timeout == 300

    def test_spike_sort_trigger(self) -> None:
        from factory.models import ProjectState

        wf = spike_sort_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "spike-sort"})
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "spike-sort"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})

    def test_spike_sort_start_node(self) -> None:
        wf = spike_sort_workflow()
        assert wf.start_node == "preprocess"

    def test_spike_sort_all_edges_unconditional(self) -> None:
        wf = spike_sort_workflow()
        for edge in wf.edges:
            assert edge.condition is None, f"Edge {edge.source}→{edge.target} has condition"


class TestQaSortingNode:
    def test_qa_sorting_node_exists(self) -> None:
        wf = spike_sort_workflow()
        assert "qa_sorting" in wf.nodes
        qa_node = wf.nodes["qa_sorting"]
        assert isinstance(qa_node, AgentNode)
        assert qa_node.role == AgentRole.HEALTH_CHECKER
        assert qa_node.model == "sonnet"
        assert qa_node.timeout == 300
        assert "sorting/" in qa_node.reads
        assert "qa_report.json" in qa_node.writes
        assert "flagged_units.json" in qa_node.writes

    def test_qa_sorting_edge(self) -> None:
        wf = spike_sort_workflow()
        edges_from_match = [e for e in wf.edges if e.source == "match"]
        assert len(edges_from_match) == 1
        assert edges_from_match[0].target == "qa_sorting"
        assert edges_from_match[0].condition is None

    def test_qa_sorting_is_terminal(self) -> None:
        wf = spike_sort_workflow()
        outgoing = [e for e in wf.edges if e.source == "qa_sorting"]
        assert len(outgoing) == 0

    def test_qa_sorting_data_flow(self) -> None:
        wf = spike_sort_workflow()
        available: set[str] = set()
        topo_order = [
            "preprocess",
            "detect_trial",
            "detect_params",
            "detect",
            "localize",
            "cluster_params",
            "cluster",
            "templates",
            "match",
            "qa_sorting",
        ]
        for node_id in topo_order:
            node = wf.nodes[node_id]
            missing = node.reads - available
            assert not missing, f"Node {node_id} has unsatisfied reads: {missing}"
            available |= node.writes
