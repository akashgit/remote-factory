"""Tests for factory.optimization.surface — Surface construction and validation."""

from __future__ import annotations

import warnings

from factory.models import FactoryConfig, OuterLoopConfig
from factory.optimization.surface import Surface
from factory.workflow.primitives import AgentNode, AgentRole, Edge, Workflow


def _make_workflow(node_ids: list[str]) -> Workflow:
    nodes = {
        nid: AgentNode(id=nid, role=AgentRole.RESEARCHER, reads=set(), writes=set())
        for nid in node_ids
    }
    edges = [
        Edge(source=node_ids[i], target=node_ids[i + 1])
        for i in range(len(node_ids) - 1)
    ] if len(node_ids) > 1 else []
    return Workflow(
        name="test",
        nodes=nodes,
        edges=edges,
        start_node=node_ids[0],
    )


class TestSurfaceConstruction:
    def test_defaults(self) -> None:
        s = Surface()
        assert s.workflow is None
        assert s.frozen_nodes == frozenset()
        assert s.prompt_slots == {}
        assert s.inner_surfaces == []
        assert s.outer_surfaces == []

    def test_with_workflow(self) -> None:
        wf = _make_workflow(["a", "b"])
        s = Surface(workflow=wf, frozen_nodes=frozenset({"a"}))
        assert s.workflow is wf
        assert s.frozen_nodes == frozenset({"a"})


class TestMutableNodes:
    def test_no_workflow(self) -> None:
        s = Surface()
        assert s.mutable_nodes() == set()

    def test_all_mutable(self) -> None:
        wf = _make_workflow(["a", "b", "c"])
        s = Surface(workflow=wf)
        assert s.mutable_nodes() == {"a", "b", "c"}

    def test_with_frozen(self) -> None:
        wf = _make_workflow(["a", "b", "c"])
        s = Surface(workflow=wf, frozen_nodes=frozenset({"b"}))
        assert s.mutable_nodes() == {"a", "c"}


class TestMutablePromptSlots:
    def test_returns_all(self) -> None:
        s = Surface(prompt_slots={"p1": "v1", "p2": "v2"})
        assert s.mutable_prompt_slots() == {"p1": "v1", "p2": "v2"}


class TestValidate:
    def test_no_issues_without_frozen(self) -> None:
        s = Surface()
        assert s.validate() == []

    def test_invalid_frozen_node_ids(self) -> None:
        wf = _make_workflow(["a", "b"])
        s = Surface(workflow=wf, frozen_nodes=frozenset({"a", "x", "y"}))
        issues = s.validate()
        assert len(issues) == 1
        assert "x" in issues[0] or "y" in issues[0]

    def test_all_frozen_warns(self) -> None:
        wf = _make_workflow(["a", "b"])
        s = Surface(workflow=wf, frozen_nodes=frozenset({"a", "b"}))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            issues = s.validate()
        assert len(issues) == 1
        assert "All nodes are frozen" in issues[0]
        assert any("All nodes are frozen" in str(warning.message) for warning in w)


class TestFromConfig:
    def test_minimal_config(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo ok",
            eval_threshold=0.8, constraints=[],
        )
        s = Surface.from_config(config)
        assert s.workflow is None
        assert s.inner_surfaces == []

    def test_with_outer_loop(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo ok",
            eval_threshold=0.8, constraints=[],
            outer_loop=OuterLoopConfig(
                inner_surfaces=["prompts/*.md"],
                outer_surfaces=["src/**/*.py"],
            ),
        )
        s = Surface.from_config(config)
        assert s.inner_surfaces == ["prompts/*.md"]
        assert s.outer_surfaces == ["src/**/*.py"]

    def test_with_workflow(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo ok",
            eval_threshold=0.8, constraints=[],
        )
        wf = _make_workflow(["a", "b"])
        s = Surface.from_config(config, workflow=wf)
        assert s.workflow is wf
