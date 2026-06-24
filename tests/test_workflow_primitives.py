"""Tier 1: Unit tests on workflow primitives."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    AgentRole,
    Edge,
    Factory,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    Verdict,
    VerdictType,
    Workflow,
)


# ── Verdict ──────────────────────────────────────────────────────


class TestVerdict:
    def test_proceed(self) -> None:
        v = Verdict.proceed()
        assert v.type == VerdictType.PROCEED
        assert v.target is None
        assert v.feedback is None

    def test_reloop(self) -> None:
        v = Verdict.reloop("researcher", "needs more depth", max_iterations=5)
        assert v.type == VerdictType.RELOOP
        assert v.target == "researcher"
        assert v.feedback == "needs more depth"
        assert v.max_iterations == 5

    def test_halt(self) -> None:
        v = Verdict.halt("critical failure")
        assert v.type == VerdictType.HALT
        assert v.reason == "critical failure"

    def test_reloop_requires_target(self) -> None:
        with pytest.raises(ValidationError):
            Verdict(type=VerdictType.RELOOP, feedback="x")

    def test_halt_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            Verdict(type=VerdictType.HALT)

    def test_serialize_roundtrip(self) -> None:
        v = Verdict.reloop("builder", "fix tests", max_iterations=3)
        data = v.model_dump()
        v2 = Verdict.model_validate(data)
        assert v2.type == v.type
        assert v2.target == v.target
        assert v2.feedback == v.feedback
        assert v2.max_iterations == v.max_iterations

    def test_proceed_serialize(self) -> None:
        v = Verdict.proceed()
        data = v.model_dump()
        v2 = Verdict.model_validate(data)
        assert v2.type == VerdictType.PROCEED

    def test_halt_serialize(self) -> None:
        v = Verdict.halt("bad output")
        data = v.model_dump()
        v2 = Verdict.model_validate(data)
        assert v2.reason == "bad output"


# ── Nodes ────────────────────────────────────────────────────────


class TestNodes:
    def test_agent_node(self) -> None:
        n = AgentNode(
            id="researcher",
            role=AgentRole.RESEARCHER,
            prompt_template="research the project",
            reads={".factory/observations.md"},
            writes={".factory/research.md"},
        )
        assert n.role == AgentRole.RESEARCHER
        assert n.blocking is True
        assert ".factory/observations.md" in n.reads

    def test_fn_node(self) -> None:
        n = FnNode(
            id="eval",
            command="factory eval /path",
            writes={".factory/eval.json"},
        )
        assert n.command == "factory eval /path"
        assert n.blocking is True

    def test_gate_node_agent(self) -> None:
        n = GateNode(
            id="gate1",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
        )
        assert n.evaluator_type == "agent"
        assert n.evaluator_role == AgentRole.CEO

    def test_gate_node_fn(self) -> None:
        n = GateNode(
            id="gate_precheck",
            evaluator_type="fn",
            evaluator_command="factory precheck /path",
        )
        assert n.evaluator_type == "fn"

    def test_gate_node_user(self) -> None:
        n = GateNode(
            id="gate_user",
            evaluator_type="user",
        )
        assert n.evaluator_type == "user"

    def test_fork_node(self) -> None:
        n = ForkNode(
            id="fork1",
            targets=["a", "b", "c"],
        )
        assert len(n.targets) == 3
        assert n.targets == ["a", "b", "c"]

    def test_join_node(self) -> None:
        n = JoinNode(
            id="join1",
            sources=["a", "b", "c"],
        )
        assert len(n.sources) == 3

    def test_study_node(self) -> None:
        n = Study(
            id="study",
            command="factory study /path",
            writes={".factory/observations.md"},
        )
        assert isinstance(n, FnNode)
        assert n.focus is None

    def test_study_with_focus(self) -> None:
        n = Study(
            id="study",
            command="factory study /path --focus auth",
            writes={".factory/observations.md"},
            focus="auth",
        )
        assert n.focus == "auth"

    def test_non_blocking_node(self) -> None:
        n = AgentNode(
            id="archivist",
            role=AgentRole.ARCHIVIST,
            prompt_template="archive",
            blocking=False,
        )
        assert n.blocking is False

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AgentNode(
                id="test",
                role=AgentRole.RESEARCHER,
                unknown_field="bad",  # type: ignore[call-arg]
            )


# ── Edge ─────────────────────────────────────────────────────────


class TestEdge:
    def test_unconditional(self) -> None:
        e = Edge(source="a", target="b")
        assert e.condition is None

    def test_conditional_proceed(self) -> None:
        e = Edge(source="gate", target="next", condition=VerdictType.PROCEED)
        assert e.condition == VerdictType.PROCEED

    def test_conditional_reloop(self) -> None:
        e = Edge(source="gate", target="prev", condition=VerdictType.RELOOP)
        assert e.condition == VerdictType.RELOOP

    def test_conditional_halt(self) -> None:
        e = Edge(source="gate", target="end", condition=VerdictType.HALT)
        assert e.condition == VerdictType.HALT


# ── Workflow ─────────────────────────────────────────────────────


class TestWorkflow:
    def _simple_workflow(self) -> Workflow:
        return Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b", reads={"a.txt"}, writes={"b.txt"}),
                "c": FnNode(id="c", command="echo c", reads={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="b"),
                Edge(source="b", target="c"),
            ],
            start_node="a",
        )

    def test_simple_valid(self) -> None:
        wf = self._simple_workflow()
        issues = wf.validate_graph()
        assert issues == []

    def test_unreachable_node(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a"),
                "b": FnNode(id="b", command="echo b"),
                "orphan": FnNode(id="orphan", command="echo orphan"),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )
        issues = wf.validate_graph()
        assert any("orphan" in i and "unreachable" in i for i in issues)

    def test_missing_edge_target(self) -> None:
        wf = Workflow(
            name="test",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[Edge(source="a", target="missing")],
            start_node="a",
        )
        issues = wf.validate_graph()
        assert any("missing" in i for i in issues)

    def test_missing_start_node(self) -> None:
        wf = Workflow(
            name="test",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[],
            start_node="nonexistent",
        )
        issues = wf.validate_graph()
        assert any("nonexistent" in i for i in issues)

    def test_reads_writes_consistency(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a"),
                "b": FnNode(id="b", command="echo b", reads={"missing.txt"}),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )
        issues = wf.validate_graph()
        assert any("missing.txt" in i for i in issues)

    def test_cycle_with_gate(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="fn", reads={"a.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="a", condition=VerdictType.RELOOP),
            ],
            start_node="a",
        )
        issues = wf.validate_graph()
        assert issues == []


# ── AgentConfig ──────────────────────────────────────────────────


class TestAgentConfig:
    def test_valid(self) -> None:
        c = AgentConfig(role=AgentRole.RESEARCHER, model="sonnet")
        assert c.role == AgentRole.RESEARCHER
        assert c.model == "sonnet"


# ── Factory ──────────────────────────────────────────────────────


class TestFactory:
    def test_select_workflow(self) -> None:
        from factory.models import ProjectState

        wf = Workflow(
            name="test",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[],
            start_node="a",
            trigger=lambda s, c: s == ProjectState.HAS_FACTORY,
        )

        factory = Factory(
            agent_pool={},
            workflows={"test": wf},
        )

        selected = factory.select_workflow(ProjectState.HAS_FACTORY)
        assert selected is not None
        assert selected.name == "test"

        none_selected = factory.select_workflow(ProjectState.NO_REPO)
        assert none_selected is None
