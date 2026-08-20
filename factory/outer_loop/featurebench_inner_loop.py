"""FeatureBenchInnerLoop — InnerLoop subclass for FeatureBench evaluation.

Wraps a candidate workflow as an ephemeral mode name, runs InnerLoop.step()
to produce a CycleRecord with full exhaust data (AgentSteps, NodeTraces,
partial credit scores).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from factory.cycle_analyzer import CycleRecord
from factory.inner_loop import Evaluator, InnerLoop
from factory.outer_loop.featurebench_evaluator import FeatureBenchEvaluator
from factory.workflow.primitives import Workflow

log = structlog.get_logger()


class FeatureBenchInnerLoop:
    """Evaluates a candidate workflow on a FeatureBench instance via InnerLoop.

    Each candidate workflow is registered as an ephemeral mode. InnerLoop.step()
    runs it as a subprocess, and CycleAnalyzer reads execution artifacts into
    a CycleRecord with full exhaust.
    """

    def __init__(
        self,
        project_dir: Path,
        mode: str,
        workflow: Workflow | None = None,
        frozen_nodes: frozenset[str] = frozenset(),
        test_command: str = "",
        test_format: str = "pytest",
        metric_path: str = "score",
    ) -> None:
        evaluator: Evaluator
        if test_format == "pytest":
            evaluator = FeatureBenchEvaluator()
        else:
            from factory.outer_loop.evaluators import get_evaluator
            evaluator = get_evaluator(test_format, metric_path=metric_path)
        self._evaluator = evaluator
        self._inner_loop = InnerLoop(
            project_dir=project_dir,
            mode=mode,
            evaluator=self._evaluator,
            workflow=workflow,
            frozen_nodes=frozen_nodes,
            test_command=test_command,
            test_format=test_format,
            metric_path=metric_path,
        )

    @property
    def project_dir(self) -> Path:
        return self._inner_loop.project_dir

    @property
    def mode(self) -> str:
        return self._inner_loop.mode

    def step(self, directives: dict | None = None) -> CycleRecord:
        """Run one evaluation cycle and return the CycleRecord with full exhaust."""
        log.info(
            "featurebench_step",
            mode=self.mode,
            project_dir=str(self.project_dir),
        )
        record = self._inner_loop.step(directives=directives)
        log.info(
            "featurebench_step_done",
            mode=self.mode,
            score_end=record.score_end,
            experiments=len(record.experiments),
            steps=len(record.steps),
        )
        return record

    def collect(self) -> CycleRecord:
        """Collect results without running a cycle."""
        return self._inner_loop.collect()

    def score_trajectory(self) -> list[float]:
        return self._inner_loop.score_trajectory()

    def total_cost(self) -> float:
        return self._inner_loop.total_cost()

    def history(self) -> list[CycleRecord]:
        return self._inner_loop.history()
