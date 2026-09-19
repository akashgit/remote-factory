"""Tests for SwarmEvaluator execution_strategy propagation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.models import SwarmConfig
from factory.workflow.primitives import AgentNode, AgentRole, Workflow


def _make_simple_workflow(name: str = "test-wf") -> Workflow:
    return Workflow(
        name=name,
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="build",
            ),
        },
        edges=[],
        start_node="builder",
    )


class TestEvaluatorPropagatesStrategy:
    """SwarmEvaluator threads execution_strategy to InnerLoop."""

    def test_compose_path_sets_execution_strategy(self, tmp_path: Path) -> None:
        """Branch A (task via compose): loop.execution_strategy = config value."""
        from factory.cycle_analyzer import CycleRecord

        config = SwarmConfig(
            benchmark="test", budget=10, execution_strategy="ceo-skill",
        )
        mock_task = MagicMock()
        config.set_task(mock_task)

        wf = _make_simple_workflow()
        mock_record = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.75, score_delta=None,
        )

        evaluator = SwarmEvaluator(
            config=config, inner_loop_factory=lambda w: "test-mode",
        )

        with (
            patch.object(SwarmEvaluator, "_create_worktree", return_value=tmp_path),
            patch.object(SwarmEvaluator, "_cleanup_worktree"),
            patch("factory.compose.compose") as mock_compose,
        ):
            mock_loop = MagicMock()
            mock_loop.step.return_value = mock_record
            mock_loop.mode = "test-mode"
            mock_compose.return_value = mock_loop

            evaluator.evaluate(wf, str(tmp_path), ["inst1"])

        # Verify execution_strategy was set on the loop
        assert mock_loop.execution_strategy == "ceo-skill"

    def test_fallback_path_passes_execution_strategy(self, tmp_path: Path) -> None:
        """Branch B (no task): execution_strategy passed as constructor kwarg."""
        from factory.cycle_analyzer import CycleRecord

        config = MagicMock(spec=SwarmConfig)
        config.get_task.return_value = None
        config.frozen_node_ids = []
        config.mandatory_node_roles = []
        config.test_command = "pytest"
        config.test_format = "pytest"
        config.metric_path = "score"
        config.execution_strategy = "ceo-tool"

        wf = _make_simple_workflow()
        mock_record = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.6, score_delta=None,
        )

        evaluator = SwarmEvaluator(
            config=config, inner_loop_factory=lambda w: "evolve-mode",
        )

        with (
            patch.object(SwarmEvaluator, "_create_worktree", return_value=tmp_path),
            patch.object(SwarmEvaluator, "_cleanup_worktree"),
            patch("factory.compose.compose") as mock_compose,
            patch("factory.inner_loop.InnerLoop") as mock_inner_loop_cls,
        ):
            mock_loop = MagicMock()
            mock_loop.step.return_value = mock_record
            mock_loop.mode = "evolve-mode"
            mock_inner_loop_cls.return_value = mock_loop

            evaluator.evaluate(wf, str(tmp_path), ["inst1"])

        mock_compose.assert_not_called()
        call_kwargs = mock_inner_loop_cls.call_args[1]
        assert call_kwargs["execution_strategy"] == "ceo-tool"

    def test_default_executor_propagated(self, tmp_path: Path) -> None:
        """When config has default strategy, 'executor' is propagated."""
        from factory.cycle_analyzer import CycleRecord

        config = SwarmConfig(benchmark="test", budget=10)
        assert config.execution_strategy == "executor"
        mock_task = MagicMock()
        config.set_task(mock_task)

        wf = _make_simple_workflow()
        mock_record = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.5, score_delta=None,
        )

        evaluator = SwarmEvaluator(
            config=config, inner_loop_factory=lambda w: "test-mode",
        )

        with (
            patch.object(SwarmEvaluator, "_create_worktree", return_value=tmp_path),
            patch.object(SwarmEvaluator, "_cleanup_worktree"),
            patch("factory.compose.compose") as mock_compose,
        ):
            mock_loop = MagicMock()
            mock_loop.step.return_value = mock_record
            mock_loop.mode = "test-mode"
            mock_compose.return_value = mock_loop

            evaluator.evaluate(wf, str(tmp_path), ["inst1"])

        assert mock_loop.execution_strategy == "executor"
