"""Tests for the SearchQA contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.searchqa import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import AgentNode, AgentRole, FnNode


class TestSearchqaWorkflow:
    """Tests for searchqa workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "searchqa"

    def test_node_count(self) -> None:
        """Workflow has exactly 3 nodes: study, builder, auto_merge."""
        wf = workflow()
        assert len(wf.nodes) == 3
        assert set(wf.nodes.keys()) == {"study", "builder", "auto_merge"}

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "study"

    def test_graph_validates(self) -> None:
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """2 edges: study→builder, builder→auto_merge."""
        wf = workflow()
        assert len(wf.edges) == 2

    def test_edges_correct(self) -> None:
        wf = workflow()
        sources_targets = [(e.source, e.target) for e in wf.edges]
        assert ("study", "builder") in sources_targets
        assert ("builder", "auto_merge") in sources_targets

    def test_study_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["study"]
        assert isinstance(node, FnNode)
        assert "task-instruction" in node.command

    def test_builder_node(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.model == "sonnet"
        assert node.timeout == 300

    def test_builder_has_seed_prompt(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "Question Answering Skill" in node.prompt_template
        assert "No learned rules yet" in node.prompt_template
        assert "<answer>" in node.prompt_template

    def test_auto_merge_node(self) -> None:
        wf = workflow()
        node = wf.nodes["auto_merge"]
        assert isinstance(node, FnNode)
        assert "git update-ref" in node.command


class TestSearchqaTerminal:
    """Tests for the terminal flag."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["searchqa"].terminal is True


class TestSearchqaTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_searchqa_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "searchqa"})

    def test_trigger_matches_without_factory(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "searchqa"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestSearchqaRegistration:
    """Tests for registration in the global workflow registry."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "searchqa" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["searchqa"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered searchqa workflow has issues: {issues}"


class TestSearchqaMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "searchqa"

    def test_meta_has_description(self) -> None:
        assert "searchqa" in meta["description"].lower() or "SearchQA" in meta["description"]
