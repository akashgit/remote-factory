"""Tests for board data contracts and parallel safety validation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from factory.workflow.compositor import (
    ParallelStep,
    SequentialStep,
    validate_composition,
    validate_composition_with_contracts,
)
from factory.workflow.primitives import FnNode, Workflow
from factory.workflow.registry import WorkflowRegistry


def _make_workflow(
    name: str,
    *,
    board_reads: list[str] | None = None,
    board_writes: list[str] | None = None,
) -> Workflow:
    node = FnNode(id="start", command="echo ok")
    return Workflow(
        name=name,
        nodes={"start": node},
        edges=[],
        start_node="start",
        board_reads=board_reads or [],
        board_writes=board_writes or [],
    )


@pytest.fixture(autouse=True)
def _reset_registry():
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


class TestBuiltinParallelRejected:
    def test_two_builtins_in_parallel_gives_error(self) -> None:
        steps = [ParallelStep(modes=["discover", "improve"])]
        errors = validate_composition(steps)
        assert any("built-in" in e and "discover" in e for e in errors)
        assert any("built-in" in e and "improve" in e for e in errors)

    def test_contract_validation_also_rejects_builtins(self) -> None:
        steps = [ParallelStep(modes=["discover", "improve"])]
        errors, _ = validate_composition_with_contracts(steps, WorkflowRegistry)
        assert any("built-in" in e for e in errors)


class TestCustomParallelOk:
    def test_two_custom_modes_no_error(self) -> None:
        wf_a = _make_workflow("custom_a", board_writes=["key_a"])
        wf_b = _make_workflow("custom_b", board_writes=["key_b"])

        def mock_get(name, path=None):
            return {"custom_a": wf_a, "custom_b": wf_b}.get(name)

        with patch.object(WorkflowRegistry, "get_workflow", side_effect=mock_get):
            steps = [ParallelStep(modes=["custom_a", "custom_b"])]
            errors, warnings = validate_composition_with_contracts(steps, WorkflowRegistry)

        assert errors == []
        assert warnings == []


class TestOverlappingWritesError:
    def test_overlapping_board_writes_in_parallel(self) -> None:
        wf_a = _make_workflow("mode_a", board_writes=["shared_key", "key_a"])
        wf_b = _make_workflow("mode_b", board_writes=["shared_key", "key_b"])

        def mock_get(name, path=None):
            return {"mode_a": wf_a, "mode_b": wf_b}.get(name)

        with patch.object(WorkflowRegistry, "get_workflow", side_effect=mock_get):
            steps = [ParallelStep(modes=["mode_a", "mode_b"])]
            errors, _ = validate_composition_with_contracts(steps, WorkflowRegistry)

        assert len(errors) >= 1
        assert any("shared_key" in e and "overlapping" in e for e in errors)


class TestNonOverlappingWritesOk:
    def test_disjoint_board_writes_in_parallel(self) -> None:
        wf_a = _make_workflow("mode_a", board_writes=["key_a"])
        wf_b = _make_workflow("mode_b", board_writes=["key_b"])

        def mock_get(name, path=None):
            return {"mode_a": wf_a, "mode_b": wf_b}.get(name)

        with patch.object(WorkflowRegistry, "get_workflow", side_effect=mock_get):
            steps = [ParallelStep(modes=["mode_a", "mode_b"])]
            errors, warnings = validate_composition_with_contracts(steps, WorkflowRegistry)

        assert errors == []


class TestSequentialUnsatisfiedReadWarns:
    def test_read_without_prior_write_warns(self) -> None:
        wf = _make_workflow("reader", board_reads=["eval_profile"])

        def mock_get(name, path=None):
            return {"reader": wf}.get(name)

        with patch.object(WorkflowRegistry, "get_workflow", side_effect=mock_get):
            steps = [SequentialStep(mode="reader")]
            errors, warnings = validate_composition_with_contracts(steps, WorkflowRegistry)

        assert errors == []
        assert len(warnings) == 1
        assert "eval_profile" in warnings[0]
        assert "not written" in warnings[0]


class TestSequentialSatisfiedReadOk:
    def test_read_satisfied_by_prior_write(self) -> None:
        wf_disc = _make_workflow("discover", board_writes=["eval_profile", "project_type"])
        wf_rev = _make_workflow("review", board_reads=["eval_profile"], board_writes=["eval_reviewed"])

        def mock_get(name, path=None):
            return {"discover": wf_disc, "review": wf_rev}.get(name)

        with patch.object(WorkflowRegistry, "get_workflow", side_effect=mock_get):
            steps = [
                SequentialStep(mode="discover"),
                SequentialStep(mode="review"),
            ]
            errors, warnings = validate_composition_with_contracts(steps, WorkflowRegistry)

        assert errors == []
        assert warnings == []


class TestBoardContractsPopulated:
    def test_five_core_workflows_have_board_contracts(self) -> None:
        from factory.workflow.definitions import (
            build_workflow,
            discover_workflow,
            improve_workflow,
            research_workflow,
            review_workflow,
        )

        discover = discover_workflow()
        assert discover.board_writes == ["eval_profile", "project_type"]
        assert discover.board_reads == []

        review = review_workflow()
        assert review.board_reads == ["eval_profile"]
        assert review.board_writes == ["eval_reviewed"]

        improve = improve_workflow()
        assert improve.board_reads == ["eval_reviewed"]
        assert improve.board_writes == ["experiment_result", "composite_score"]

        build = build_workflow()
        assert build.board_writes == ["build_spec", "scaffold_complete"]
        assert build.board_reads == []

        research = research_workflow()
        assert research.board_reads == ["eval_reviewed"]
        assert research.board_writes == ["research_metric", "failure_analysis"]
