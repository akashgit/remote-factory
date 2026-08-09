"""Slimmed CycleAnalyzer producing StepRecord for the optimization loop.

Keeps the 4-tier artifact reading pattern from cycle_analyzer.py:
1. events.jsonl — agent invocations and cycle events
2. results.tsv — experiment scores and verdicts
3. eval artifacts — per-experiment eval output files
4. DAG node trace — workflow node-to-artifact mapping
"""

from __future__ import annotations

from pathlib import Path

from factory.cycle_analyzer import CycleAnalyzer
from factory.optimization.types import StepRecord
from factory.workflow.primitives import Workflow


class StepAnalyzer:
    """Reads .factory/ artifacts and produces StepRecord for optimizers."""

    def __init__(
        self,
        factory_dir: Path,
        workflow: Workflow | None = None,
    ) -> None:
        self.factory_dir = Path(factory_dir)
        self.workflow = workflow
        self._inner = CycleAnalyzer(factory_dir, workflow=workflow)

    def latest(self) -> StepRecord | None:
        """Read the latest cycle and convert to a StepRecord."""
        record = self._inner.latest()
        if record is None:
            return None
        return StepRecord(
            step_number=record.cycle_number,
            score_start=record.score_start,
            score_end=record.score_end,
            score_delta=record.score_delta,
            duration_s=record.duration_s,
            cost_usd=record.total_cost_usd,
            verdict=self._summarize_verdict(record.kept, record.reverted, record.errored),
            artifacts=record.eval_artifacts,
        )

    def all_steps(self) -> list[StepRecord]:
        """Read all cycles and convert to StepRecords."""
        records = self._inner.analyze()
        return [
            StepRecord(
                step_number=r.cycle_number,
                score_start=r.score_start,
                score_end=r.score_end,
                score_delta=r.score_delta,
                duration_s=r.duration_s,
                cost_usd=r.total_cost_usd,
                verdict=self._summarize_verdict(r.kept, r.reverted, r.errored),
                artifacts=r.eval_artifacts,
            )
            for r in records
        ]

    @staticmethod
    def _summarize_verdict(kept: int, reverted: int, errored: int) -> str | None:
        if kept + reverted + errored == 0:
            return None
        if errored > 0:
            return "error"
        if kept > reverted:
            return "keep"
        return "revert"
