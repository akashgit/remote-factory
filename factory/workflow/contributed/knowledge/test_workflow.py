"""Tests for the knowledge contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.knowledge import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestKnowledgeWorkflow:
    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "knowledge"

    def test_node_count(self) -> None:
        wf = workflow()
        assert len(wf.nodes) == 7
        assert set(wf.nodes.keys()) == {
            "observe",
            "extract_deterministic",
            "extract_llm",
            "update_graph",
            "analyst",
            "gate_insights",
            "report",
        }

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "observe"

    def test_graph_validates(self) -> None:
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        wf = workflow()
        assert len(wf.edges) == 7

    def test_observe_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["observe"]
        assert isinstance(node, FnNode)
        assert "task_config.json" in node.command

    def test_extract_deterministic_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["extract_deterministic"]
        assert isinstance(node, FnNode)
        assert "extract_from_tool_calls" in node.command

    def test_extract_llm_is_researcher(self) -> None:
        wf = workflow()
        node = wf.nodes["extract_llm"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_update_graph_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["update_graph"]
        assert isinstance(node, FnNode)
        assert "append_triplets" in node.command

    def test_analyst_is_knowledge_analyst(self) -> None:
        wf = workflow()
        node = wf.nodes["analyst"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.KNOWLEDGE_ANALYST
        assert node.timeout == 900

    def test_gate_insights_is_fn_evaluator(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_insights"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command

    def test_report_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["report"]
        assert isinstance(node, FnNode)
        assert "format_insights" in node.command

    def test_reloop_edge_exists(self) -> None:
        wf = workflow()
        reloop_edges = [
            e
            for e in wf.edges
            if e.source == "gate_insights"
            and e.target == "observe"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_proceed_edge_exists(self) -> None:
        wf = workflow()
        proceed_edges = [
            e
            for e in wf.edges
            if e.source == "gate_insights"
            and e.target == "report"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1


class TestKnowledgeTerminal:
    def test_terminal_flag(self) -> None:
        wf = workflow()
        assert wf.terminal is True


class TestKnowledgeTrigger:
    def test_trigger_accepts_knowledge_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "knowledge"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestKnowledgeRegistration:
    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "knowledge" in workflows

    def test_registered_workflow_validates(self) -> None:
        workflows = register_all()
        wf = workflows["knowledge"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered workflow has validation issues: {issues}"


class TestKnowledgeMeta:
    def test_meta_has_name(self) -> None:
        assert meta["name"] == "knowledge"

    def test_meta_has_description(self) -> None:
        assert "description" in meta
        assert len(str(meta["description"])) > 20


# ── tau-bench workflow tests ────────────────────────────────────


class TestTauBenchWorkflow:
    def _wf(self):  # noqa: ANN202
        from factory.workflow.contributed.knowledge.workflow import tau_workflow

        return tau_workflow()

    def test_workflow_name(self) -> None:
        assert self._wf().name == "knowledge-tau"

    def test_node_count(self) -> None:
        wf = self._wf()
        assert len(wf.nodes) == 11
        assert set(wf.nodes.keys()) == {
            "run_eval",
            "extract_tau",
            "extract_llm",
            "update_graph",
            "analyst",
            "gate_insights",
            "gate_score",
            "improve",
            "re_eval",
            "gate_compare",
            "report",
        }

    def test_start_node(self) -> None:
        assert self._wf().start_node == "run_eval"

    def test_graph_validates(self) -> None:
        issues = self._wf().validate_graph()
        assert issues == [], f"Tau workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        assert len(self._wf().edges) == 13

    def test_run_eval_is_fn(self) -> None:
        node = self._wf().nodes["run_eval"]
        assert isinstance(node, FnNode)
        assert "run_tau_eval" in node.command

    def test_extract_tau_is_fn(self) -> None:
        node = self._wf().nodes["extract_tau"]
        assert isinstance(node, FnNode)
        assert "parse_simulation" in node.command

    def test_gate_score_is_fn_evaluator(self) -> None:
        node = self._wf().nodes["gate_score"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert "score_threshold" in (node.evaluator_command or "")

    def test_improve_is_builder(self) -> None:
        node = self._wf().nodes["improve"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_re_eval_is_fn(self) -> None:
        node = self._wf().nodes["re_eval"]
        assert isinstance(node, FnNode)
        assert "run_tau_eval" in node.command

    def test_gate_compare_is_fn_evaluator(self) -> None:
        node = self._wf().nodes["gate_compare"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert "baseline_score" in (node.evaluator_command or "")

    def test_gate_insights_proceed_to_gate_score(self) -> None:
        wf = self._wf()
        edges = [
            e
            for e in wf.edges
            if e.source == "gate_insights"
            and e.target == "gate_score"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(edges) == 1

    def test_gate_insights_reloop_to_run_eval(self) -> None:
        wf = self._wf()
        edges = [
            e
            for e in wf.edges
            if e.source == "gate_insights"
            and e.target == "run_eval"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(edges) == 1

    def test_gate_score_proceed_to_report(self) -> None:
        wf = self._wf()
        edges = [
            e
            for e in wf.edges
            if e.source == "gate_score"
            and e.target == "report"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(edges) == 1

    def test_gate_score_reloop_to_improve(self) -> None:
        wf = self._wf()
        edges = [
            e
            for e in wf.edges
            if e.source == "gate_score"
            and e.target == "improve"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(edges) == 1

    def test_gate_compare_proceed_to_report(self) -> None:
        wf = self._wf()
        edges = [
            e
            for e in wf.edges
            if e.source == "gate_compare"
            and e.target == "report"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(edges) == 1

    def test_gate_compare_reloop_to_improve(self) -> None:
        wf = self._wf()
        edges = [
            e
            for e in wf.edges
            if e.source == "gate_compare"
            and e.target == "improve"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(edges) == 1

    def test_terminal_flag(self) -> None:
        assert self._wf().terminal is True


class TestTauBenchTrigger:
    def test_trigger_accepts_knowledge_tau(self) -> None:
        from factory.workflow.contributed.knowledge.workflow import tau_workflow

        wf = tau_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "knowledge-tau"})

    def test_trigger_rejects_other_modes(self) -> None:
        from factory.workflow.contributed.knowledge.workflow import tau_workflow

        wf = tau_workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "knowledge"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestTauBenchRegistration:
    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "knowledge-tau" in workflows

    def test_registered_workflow_validates(self) -> None:
        workflows = register_all()
        wf = workflows["knowledge-tau"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered tau workflow has validation issues: {issues}"


class TestTauBenchMeta:
    def test_meta_has_name(self) -> None:
        from factory.workflow.contributed.knowledge.workflow import tau_meta

        assert tau_meta["name"] == "knowledge-tau"

    def test_meta_has_description(self) -> None:
        from factory.workflow.contributed.knowledge.workflow import tau_meta

        assert "description" in tau_meta
        assert len(str(tau_meta["description"])) > 20
