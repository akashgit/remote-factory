"""Tests for the analyze-optimize workflow definition."""

from __future__ import annotations


from factory.models import ProjectState
from factory.workflow.definitions import analyze_optimize_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestAnalyzeOptimizeValid:
    """Graph validation tests."""

    def test_graph_validates(self) -> None:
        wf = analyze_optimize_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"analyze-optimize workflow has issues: {issues}"

    def test_correct_name(self) -> None:
        wf = analyze_optimize_workflow()
        assert wf.name == "analyze-optimize"

    def test_start_node_is_run_eval(self) -> None:
        wf = analyze_optimize_workflow()
        assert wf.start_node == "run_eval"

    def test_has_exactly_9_nodes(self) -> None:
        wf = analyze_optimize_workflow()
        assert len(wf.nodes) == 9, f"Expected 9 nodes, got {len(wf.nodes)}: {list(wf.nodes.keys())}"

    def test_node_ids(self) -> None:
        wf = analyze_optimize_workflow()
        expected = {
            "run_eval",
            "researcher_reflect",
            "analyst",
            "gate_insights",
            "strategist_curate",
            "builder",
            "re_eval",
            "gate_compare",
            "report",
        }
        assert set(wf.nodes.keys()) == expected


class TestAnalyzeOptimizeNodeTypes:
    """Verify each node has the correct type and role."""

    def test_run_eval_is_fn_node(self) -> None:
        wf = analyze_optimize_workflow()
        assert isinstance(wf.nodes["run_eval"], FnNode)

    def test_researcher_reflect_is_researcher(self) -> None:
        wf = analyze_optimize_workflow()
        node = wf.nodes["researcher_reflect"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_analyst_is_strategist(self) -> None:
        wf = analyze_optimize_workflow()
        node = wf.nodes["analyst"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_gate_insights_is_fn_gate(self) -> None:
        wf = analyze_optimize_workflow()
        node = wf.nodes["gate_insights"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"

    def test_strategist_curate_is_strategist(self) -> None:
        wf = analyze_optimize_workflow()
        node = wf.nodes["strategist_curate"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_builder_is_builder(self) -> None:
        wf = analyze_optimize_workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_re_eval_is_fn_node(self) -> None:
        wf = analyze_optimize_workflow()
        assert isinstance(wf.nodes["re_eval"], FnNode)

    def test_gate_compare_is_fn_gate(self) -> None:
        wf = analyze_optimize_workflow()
        node = wf.nodes["gate_compare"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"

    def test_report_is_fn_node(self) -> None:
        wf = analyze_optimize_workflow()
        assert isinstance(wf.nodes["report"], FnNode)


class TestAnalyzeOptimizeEdges:
    """Verify edge topology."""

    def test_has_10_edges(self) -> None:
        wf = analyze_optimize_workflow()
        assert len(wf.edges) == 10

    def test_gate_insights_reloop_to_run_eval(self) -> None:
        wf = analyze_optimize_workflow()
        reloop = [
            e for e in wf.edges if e.source == "gate_insights" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "run_eval"

    def test_gate_insights_proceed_to_curate(self) -> None:
        wf = analyze_optimize_workflow()
        proceed = [
            e
            for e in wf.edges
            if e.source == "gate_insights" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1
        assert proceed[0].target == "strategist_curate"

    def test_gate_compare_reloop_to_analyst(self) -> None:
        wf = analyze_optimize_workflow()
        reloop = [
            e for e in wf.edges if e.source == "gate_compare" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "analyst"

    def test_gate_compare_proceed_to_report(self) -> None:
        wf = analyze_optimize_workflow()
        proceed = [
            e for e in wf.edges if e.source == "gate_compare" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1
        assert proceed[0].target == "report"

    def test_no_halt_edges(self) -> None:
        wf = analyze_optimize_workflow()
        halt = [e for e in wf.edges if e.condition == VerdictType.HALT]
        assert halt == []

    def test_report_is_terminal(self) -> None:
        wf = analyze_optimize_workflow()
        outgoing = [e for e in wf.edges if e.source == "report"]
        assert outgoing == []


class TestAnalyzeOptimizeTrigger:
    """Trigger function tests."""

    def test_requires_explicit_mode(self) -> None:
        wf = analyze_optimize_workflow()
        assert wf.trigger is not None
        assert wf.trigger(
            ProjectState.HAS_FACTORY,
            {"mode": "analyze-optimize"},
        )

    def test_rejects_without_mode(self) -> None:
        wf = analyze_optimize_workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})

    def test_rejects_wrong_mode(self) -> None:
        wf = analyze_optimize_workflow()
        assert wf.trigger is not None
        assert not wf.trigger(
            ProjectState.HAS_FACTORY,
            {"mode": "improve"},
        )

    def test_works_regardless_of_state(self) -> None:
        wf = analyze_optimize_workflow()
        assert wf.trigger is not None
        for state in ProjectState:
            assert wf.trigger(state, {"mode": "analyze-optimize"})


class TestAnalyzeOptimizeRegistration:
    """Registration tests."""

    def test_in_registry(self) -> None:
        registry = register_all()
        assert "analyze-optimize" in registry

    def test_registry_name(self) -> None:
        registry = register_all()
        assert registry["analyze-optimize"].name == "analyze-optimize"

    def test_trigger_exists(self) -> None:
        registry = register_all()
        assert registry["analyze-optimize"].trigger is not None


class TestAnalyzeOptimizeNoDeepQA:
    """Verify no deep-QA pipeline nodes."""

    def test_no_deep_qa_nodes(self) -> None:
        wf = analyze_optimize_workflow()
        deep_qa = {
            "health_checker",
            "code_reviewer",
            "adversarial_tester",
            "gate_qa",
            "gate_precheck",
        }
        present = deep_qa & set(wf.nodes.keys())
        assert present == set(), f"Deep-QA nodes should not be present: {present}"


class TestAnalyzeOptimizeFullyHeadless:
    """Verify all gates are fn evaluators."""

    def test_all_gates_are_fn(self) -> None:
        wf = analyze_optimize_workflow()
        gates = [n for n in wf.nodes.values() if isinstance(n, GateNode)]
        assert len(gates) == 2
        for gate in gates:
            assert gate.evaluator_type == "fn", (
                f"Gate {gate.id} is {gate.evaluator_type}, expected fn"
            )

    def test_no_archivist(self) -> None:
        wf = analyze_optimize_workflow()
        archivist = [
            n
            for n in wf.nodes.values()
            if isinstance(n, AgentNode) and n.role == AgentRole.ARCHIVIST
        ]
        assert archivist == []
