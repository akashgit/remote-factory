"""Tests for factory/workflow/overwrite.py — runtime workflow mutation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.workflow.overwrite import _apply_mutations, _parse_mutations, generate_session_skill
from factory.workflow.primitives import AgentNode, AgentRole, Edge, FnNode, Workflow


def _minimal_workflow() -> Workflow:
    """A minimal workflow with a builder whose prompt omits 'run tests'."""
    return Workflow(
        name="test-tune",
        nodes={
            "study": FnNode(id="study", command="factory study $PROJECT_PATH"),
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="Implement the feature. Commit changes.",
            ),
            "archivist": AgentNode(
                id="archivist",
                role=AgentRole.ARCHIVIST,
                prompt_template="Archive results.",
                model="haiku",
            ),
        },
        edges=[
            Edge(source="study", target="builder"),
            Edge(source="builder", target="archivist"),
        ],
        start_node="study",
    )


class TestApplyMutationsUpdateNode:
    def test_update_prompt_template(self) -> None:
        wf = _minimal_workflow()
        mutations = [
            {"op": "update_node", "node_id": "builder", "field": "prompt_template",
             "value": "Implement the feature. Run pytest. Commit changes."},
        ]
        result = _apply_mutations(wf, mutations)
        node = result.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "Run pytest" in node.prompt_template

    def test_update_timeout(self) -> None:
        wf = _minimal_workflow()
        mutations = [
            {"op": "update_node", "node_id": "builder", "field": "timeout", "value": 900},
        ]
        result = _apply_mutations(wf, mutations)
        node = result.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.timeout == 900

    def test_update_nonexistent_node_raises(self) -> None:
        wf = _minimal_workflow()
        mutations = [
            {"op": "update_node", "node_id": "nonexistent", "field": "timeout", "value": 900},
        ]
        with pytest.raises(KeyError, match="nonexistent"):
            _apply_mutations(wf, mutations)

    def test_update_nonexistent_field_raises(self) -> None:
        wf = _minimal_workflow()
        mutations = [
            {"op": "update_node", "node_id": "builder", "field": "bogus_field", "value": "x"},
        ]
        with pytest.raises(KeyError, match="bogus_field"):
            _apply_mutations(wf, mutations)


class TestApplyMutationsRemoveNode:
    def test_remove_node_and_edges(self) -> None:
        wf = _minimal_workflow()
        mutations = [{"op": "remove_node", "node_id": "archivist"}]
        result = _apply_mutations(wf, mutations)
        assert "archivist" not in result.nodes
        for edge in result.edges:
            assert edge.source != "archivist"
            assert edge.target != "archivist"

    def test_remove_nonexistent_node_raises(self) -> None:
        wf = _minimal_workflow()
        mutations = [{"op": "remove_node", "node_id": "ghost"}]
        with pytest.raises(KeyError, match="ghost"):
            _apply_mutations(wf, mutations)


class TestApplyMutationsAddEdge:
    def test_add_edge(self) -> None:
        wf = _minimal_workflow()
        mutations = [{"op": "add_edge", "source": "study", "target": "archivist"}]
        result = _apply_mutations(wf, mutations)
        added = [e for e in result.edges if e.source == "study" and e.target == "archivist"]
        assert len(added) == 1


class TestApplyMutationsRemoveEdge:
    def test_remove_existing_edge(self) -> None:
        wf = _minimal_workflow()
        mutations = [{"op": "remove_edge", "source": "builder", "target": "archivist"}]
        result = _apply_mutations(wf, mutations)
        removed = [e for e in result.edges if e.source == "builder" and e.target == "archivist"]
        assert len(removed) == 0

    def test_remove_nonexistent_edge_warns(self) -> None:
        wf = _minimal_workflow()
        mutations = [{"op": "remove_edge", "source": "study", "target": "archivist"}]
        result = _apply_mutations(wf, mutations)
        assert len(result.edges) == 2


class TestApplyMutationsUnknownOp:
    def test_unknown_op_raises(self) -> None:
        wf = _minimal_workflow()
        mutations = [{"op": "teleport_node", "node_id": "builder"}]
        with pytest.raises(ValueError, match="Unknown mutation op"):
            _apply_mutations(wf, mutations)


class TestParseMutations:
    def test_parse_clean_json(self) -> None:
        raw = '[{"op": "update_node", "node_id": "builder", "field": "timeout", "value": 300}]'
        result = _parse_mutations(raw)
        assert len(result) == 1
        assert result[0]["op"] == "update_node"

    def test_parse_json_with_surrounding_text(self) -> None:
        raw = 'Here are the mutations:\n[{"op": "remove_node", "node_id": "archivist"}]\nDone.'
        result = _parse_mutations(raw)
        assert len(result) == 1

    def test_parse_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="No JSON array"):
            _parse_mutations("no json here")


class TestGenerateSessionSkill:
    def test_generates_skill_md(self, tmp_path: Path) -> None:
        wf = _minimal_workflow()
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        result = generate_session_skill(wf, "test-tune", tmp_path)
        assert result.exists()
        assert result.name == "SKILL.md"
        content = result.read_text()
        assert len(content) > 50


class TestOverwriteForwardedThroughTmux:
    def test_build_tmux_run_args_includes_overwrite(self) -> None:
        import argparse

        from factory.cli._tmux_commands import _build_tmux_run_args

        args = argparse.Namespace(
            mode="design",
            no_github=False,
            profile=None,
            focus=None,
            refine=None,
            clean_pr=None,
            runner=None,
            prompt=None,
            branch=None,
            min_growth=None,
            max_new=None,
            discover_only=False,
            bg_agents=False,
            tmux_persist=False,
            use_profile=False,
            overwrite="skip adversarial testing",
        )
        result = _build_tmux_run_args(args, Path("/tmp/proj"), model=None)
        assert "--overwrite" in result
        assert "skip adversarial testing" in result


class TestTuneWorkflow:
    """E2E test: a tune loop discovers missing test instructions and fixes them."""

    def test_tune_workflow(self, tmp_path: Path) -> None:
        wf = _minimal_workflow()
        assert "run tests" not in wf.nodes["builder"].prompt_template  # type: ignore[union-attr]

        mock_stdout = (
            '[{"op": "update_node", "node_id": "builder", '
            '"field": "prompt_template", '
            '"value": "Implement the feature. Run tests with pytest -v. Commit changes."}]'
        )

        with patch("factory.agents.runner.invoke_agent", new_callable=AsyncMock, return_value=(mock_stdout, 0)):
            from factory.workflow.overwrite import apply_overwrite

            mutated = apply_overwrite(
                wf,
                "The builder should always run tests after implementing",
                tmp_path,
            )

        builder = mutated.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "Run tests" in builder.prompt_template
        assert "pytest" in builder.prompt_template

        skill_path = generate_session_skill(mutated, "test-tune", tmp_path)
        skill_content = skill_path.read_text()
        assert "Run tests" in skill_content or "pytest" in skill_content
