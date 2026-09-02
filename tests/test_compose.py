"""Tests for factory/compose.py — composition validation, capability inference."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.compose import (
    Capability,
    IncompatibleCompositionError,
    ModeCapabilities,
    TaskCapabilities,
    TaskProtocol,
    compose,
    validate_composition,
)
from factory.task import (
    ExactMatchScoring,
    ExitCodeScoring,
    JSONScoring,
    PytestScoring,
    Task,
    TaskDefinition,
)


# ── Capability StrEnum tests ────────────────────────────────────


class TestCapability:
    def test_members(self):
        assert Capability.CAN_MODIFY_CODE == "can_modify_code"
        assert Capability.HAS_BUILDER == "has_builder"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            Capability("nonexistent_capability")


# ── ModeCapabilities tests ──────────────────────────────────────


def _make_workflow(**node_kwargs):
    """Build a minimal Workflow with specified nodes."""
    from factory.workflow.primitives import (
        AgentNode,
        AgentRole,
        Edge,
        FnNode,
        ForkNode,
        GateNode,
        Workflow,
    )

    nodes: dict[str, AgentNode | FnNode | GateNode | ForkNode] = {}

    if node_kwargs.get("builder"):
        nodes["builder"] = AgentNode(
            id="builder", role=AgentRole.BUILDER, prompt_template="build"
        )
    if node_kwargs.get("researcher"):
        nodes["researcher"] = AgentNode(
            id="researcher", role=AgentRole.RESEARCHER, prompt_template="research"
        )
    if node_kwargs.get("strategist"):
        nodes["strategist"] = AgentNode(
            id="strategist", role=AgentRole.STRATEGIST, prompt_template="strategize"
        )
    if node_kwargs.get("code_reviewer"):
        nodes["code_reviewer"] = AgentNode(
            id="code_reviewer", role=AgentRole.CODE_REVIEWER, prompt_template="review"
        )
    if node_kwargs.get("gate"):
        nodes["gate"] = GateNode(id="gate", gate_prompt="check quality")
    if node_kwargs.get("fork"):
        targets = list(nodes.keys())[:1] or ["builder"]
        nodes["fork"] = ForkNode(id="fork", targets=targets)
    if node_kwargs.get("fn"):
        nodes["fn"] = FnNode(id="fn", command="echo test")

    # Build edges (simple chain)
    edges = []
    node_ids = list(nodes.keys())
    for i in range(len(node_ids) - 1):
        edges.append(Edge(source=node_ids[i], target=node_ids[i + 1]))

    return Workflow(
        name=node_kwargs.get("name", "test-workflow"),
        nodes=nodes,
        edges=edges,
        start_node=node_ids[0] if node_ids else "start",
    )


class TestModeCapabilities:
    def test_builder_provides_code_and_tests(self):
        wf = _make_workflow(builder=True)
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.CAN_MODIFY_CODE in caps.provides
        assert Capability.HAS_BUILDER in caps.provides
        assert Capability.CAN_RUN_TESTS in caps.provides

    def test_researcher_provides_researcher(self):
        wf = _make_workflow(researcher=True)
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.HAS_RESEARCHER in caps.provides

    def test_gate_provides_quality_gate(self):
        wf = _make_workflow(builder=True, gate=True)
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.HAS_QUALITY_GATE in caps.provides

    def test_fork_provides_parallelism(self):
        wf = _make_workflow(builder=True, fork=True)
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.HAS_PARALLELISM in caps.provides

    def test_fn_provides_subprocess(self):
        wf = _make_workflow(fn=True)
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.CAN_RUN_SUBPROCESS in caps.provides

    def test_empty_workflow(self):
        from factory.workflow.primitives import Workflow

        wf = Workflow(name="empty", nodes={}, edges=[], start_node="start")
        caps = ModeCapabilities.from_workflow(wf)
        assert len(caps.provides) == 0


# ── TaskCapabilities tests ──────────────────────────────────────


class TestTaskCapabilities:
    def test_pytest_requires_builder(self):
        task = Task(definition=TaskDefinition(name="t", scoring=PytestScoring()))
        caps = TaskCapabilities.from_task(task)
        assert Capability.CAN_MODIFY_CODE in caps.requires
        assert Capability.CAN_RUN_TESTS in caps.requires
        assert Capability.HAS_BUILDER in caps.requires

    def test_exit_code_requires_builder(self):
        task = Task(definition=TaskDefinition(name="t", scoring=ExitCodeScoring()))
        caps = TaskCapabilities.from_task(task)
        assert Capability.CAN_MODIFY_CODE in caps.requires

    def test_json_scoring_minimal_requirements(self):
        task = Task(definition=TaskDefinition(name="t", scoring=JSONScoring()))
        caps = TaskCapabilities.from_task(task)
        assert Capability.CAN_MODIFY_CODE not in caps.requires

    def test_exact_match_requires_subprocess(self):
        task = Task(definition=TaskDefinition(name="t", scoring=ExactMatchScoring()))
        caps = TaskCapabilities.from_task(task)
        assert Capability.CAN_RUN_SUBPROCESS in caps.requires


# ── validate_composition tests ───────────────────────────────────


class TestValidateComposition:
    def test_compatible(self):
        """Builder workflow + pytest task = compatible."""
        wf = _make_workflow(builder=True)
        task = Task(definition=TaskDefinition(name="t", scoring=PytestScoring()))
        # Should not raise
        validate_composition(wf, task)

    def test_incompatible_research_only(self):
        """Research-only workflow + pytest task = incompatible."""
        wf = _make_workflow(researcher=True, name="research")
        task = Task(definition=TaskDefinition(name="t", scoring=PytestScoring()))
        with pytest.raises(IncompatibleCompositionError) as exc_info:
            validate_composition(wf, task)
        assert "can_modify_code" in str(exc_info.value).lower() or "has_builder" in str(exc_info.value).lower()

    def test_json_task_with_any_workflow(self):
        """JSON scoring requires minimal capabilities."""
        wf = _make_workflow(researcher=True, fn=True)
        task = Task(definition=TaskDefinition(name="t", scoring=JSONScoring()))
        # Should not raise — JSON scoring has no requirements
        validate_composition(wf, task)

    def test_error_message_has_mode_and_task_name(self):
        wf = _make_workflow(researcher=True, name="design")
        task = Task(definition=TaskDefinition(name="chess-evolve", scoring=PytestScoring()))
        with pytest.raises(IncompatibleCompositionError) as exc_info:
            validate_composition(wf, task)
        err = exc_info.value
        assert err.mode_name == "design"
        assert err.task_name == "chess-evolve"
        assert len(err.mode_missing) > 0


# ── compose() tests ─────────────────────────────────────────────


class TestCompose:
    def test_compose_returns_inner_loop(self, tmp_path: Path):
        wf = _make_workflow(builder=True)
        task = Task(definition=TaskDefinition(name="t", scoring=PytestScoring()))
        loop = compose(wf, task, tmp_path)
        from factory.inner_loop import InnerLoop

        assert isinstance(loop, InnerLoop)

    def test_compose_incompatible_raises(self, tmp_path: Path):
        wf = _make_workflow(researcher=True, name="research-only")
        task = Task(definition=TaskDefinition(name="t", scoring=PytestScoring()))
        with pytest.raises(IncompatibleCompositionError):
            compose(wf, task, tmp_path)

    def test_compose_non_task_raises(self, tmp_path: Path):
        wf = _make_workflow(builder=True)
        with pytest.raises(TypeError, match="four hooks"):
            compose(wf, "not a task", tmp_path)

    def test_compose_sets_task_on_loop(self, tmp_path: Path):
        wf = _make_workflow(builder=True)
        task = Task(definition=TaskDefinition(name="t", scoring=PytestScoring()))
        loop = compose(wf, task, tmp_path)
        assert loop.task is task


# ── TaskProtocol tests ──────────────────────────────────────────


class TestTaskProtocol:
    def test_task_satisfies_protocol(self):
        task = Task()
        assert isinstance(task, TaskProtocol)

    def test_plain_object_not_protocol(self):
        assert not isinstance("hello", TaskProtocol)


# ── Composition Matrix ──────────────────────────────────────────


class TestCompositionMatrix:
    """Test known mode × task combinations."""

    @pytest.mark.parametrize(
        "workflow_kwargs,scoring,should_pass",
        [
            # Full workflow (builder + gate) × pytest → pass
            (dict(builder=True, gate=True), PytestScoring(), True),
            # Builder-only × pytest → pass
            (dict(builder=True), PytestScoring(), True),
            # Research-only × pytest → fail
            (dict(researcher=True), PytestScoring(), False),
            # Builder × exit_code → pass
            (dict(builder=True), ExitCodeScoring(), True),
            # Builder × json → pass
            (dict(builder=True), JSONScoring(), True),
            # Builder × exact_match → pass
            (dict(builder=True), ExactMatchScoring(), True),
            # Research × json → pass (no code mod needed)
            (dict(researcher=True, fn=True), JSONScoring(), True),
            # Empty workflow × pytest → fail
            (dict(fn=True), PytestScoring(), False),
            # Research × exact_match → pass (only needs subprocess)
            (dict(researcher=True, fn=True), ExactMatchScoring(), True),
        ],
    )
    def test_composition(self, workflow_kwargs, scoring, should_pass):
        wf = _make_workflow(**workflow_kwargs)
        task = Task(definition=TaskDefinition(name="t", scoring=scoring))
        if should_pass:
            validate_composition(wf, task)  # should not raise
        else:
            with pytest.raises(IncompatibleCompositionError):
                validate_composition(wf, task)
