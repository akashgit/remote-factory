"""InnerLoop — model-like wrapper for mode + evaluator that an outer-loop optimizer calls.

CycleAnalyzer handles execution tracing (what agents ran, costs, verdicts).
Evaluator handles score interpretation (parses evaluator-specific output artifacts).
InnerLoop composes both. The Executor protocol decouples the execution backend
from cycle orchestration — FactoryCeoExecutor runs `factory ceo` as a subprocess,
but any backend (SkillOpt, custom benchmarks) can plug in via a custom Executor.

Usage:
    evaluator = CirclePackingEvaluator()
    loop = InnerLoop(project_dir, mode="evolve", evaluator=evaluator)

    for i in range(budget):
        result = loop.step()
        if result.score_end > target:
            break
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from factory.cycle_analyzer import CycleAnalyzer, CycleRecord
from factory.workflow.primitives import Workflow


@dataclass
class EvalResult:
    """Structured evaluator output."""

    score: float
    metrics: dict[str, float] = field(default_factory=dict)
    valid: bool = True
    artifacts: list[str] = field(default_factory=list)


@runtime_checkable
class Evaluator(Protocol):
    """Interface for parsing evaluator-specific output artifacts.

    Each implementation knows the output format of one evaluator.
    It reads artifact files that the inner loop already produced —
    it doesn't run the evaluator itself.
    """

    def parse(self, artifact_path: Path) -> EvalResult:
        """Parse an evaluator output artifact into a structured EvalResult."""
        ...

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        """Parse multiple artifacts, returning the most recent/best result."""
        ...

    def get_info(self) -> dict:
        """Return static info about this evaluator (name, target, etc.)."""
        ...


@runtime_checkable
class Executor(Protocol):
    """Interface for inner-loop execution backends.

    FactoryCeoExecutor runs `factory ceo` as a subprocess.
    Custom executors (e.g. SkillOpt) implement their own execution logic.
    """

    def execute(
        self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
    ) -> CycleRecord:
        ...


class FactoryCeoExecutor:
    """Runs `factory ceo` as a subprocess and collects results via CycleAnalyzer."""

    def __init__(self, mode: str = "evolve", workflow: Workflow | None = None) -> None:
        self.mode = mode
        self.workflow = workflow
        self._directive_count = 0

    def execute(
        self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
    ) -> CycleRecord:
        factory_dir = project_dir / ".factory"

        if directives:
            self._write_directives(factory_dir, directives)

        result = subprocess.run(
            [sys.executable, "-m", "factory", "ceo", str(project_dir),
             "--mode", self.mode, "--no-worktree"],
            cwd=project_dir,
        )

        analyzer = CycleAnalyzer(factory_dir, workflow=self.workflow)
        record = analyzer.latest()
        if record is None:
            record = CycleRecord(
                cycle_number=0,
                mode=self.mode,
                started_at=None,
                ended_at=None,
                duration_s=0,
                score_start=None,
                score_end=None,
                score_delta=None,
            )

        if record.mode is None:
            record.mode = self.mode

        if result.returncode != 0:
            record.errored = (record.errored or 0) + 1

        return record

    def _write_directives(self, factory_dir: Path, directives: dict[str, Any]) -> None:
        msg_dir = factory_dir / "messages"
        msg_dir.mkdir(parents=True, exist_ok=True)
        msg_id = f"outer-loop-{self._directive_count:04d}"
        msg_path = msg_dir / f"{msg_id}.md"

        lines = ["# Outer Loop Directives\n"]
        for key, value in directives.items():
            if isinstance(value, list):
                lines.append(f"- **{key}:** {', '.join(str(v) for v in value)}")
            else:
                lines.append(f"- **{key}:** {value}")

        msg_path.write_text("\n".join(lines) + "\n")
        self._directive_count += 1


class CirclePackingEvaluator:
    """Parses output artifacts from skydiscover's circle packing evaluator.

    Knows how to read JSON files with the schema:
        {sum_radii, target_ratio, validity, eval_time, combined_score}
    """

    def __init__(self, target: float = 2.635) -> None:
        self.target = target

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(Path(artifact_path).read_text())
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)
        return EvalResult(
            score=float(data.get("combined_score", 0.0)),
            metrics={k: float(v) for k, v in data.items() if isinstance(v, (int, float))},
            valid=data.get("validity", 0.0) == 1.0,
            artifacts=[str(artifact_path)],
        )

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        best = EvalResult(score=0.0, valid=False)
        for p in artifact_paths:
            result = self.parse(p)
            if result.score > best.score:
                best = result
        return best

    def get_info(self) -> dict:
        return {
            "benchmark": "circle_packing",
            "target": self.target,
            "metrics": ["sum_radii", "target_ratio", "validity", "eval_time", "combined_score"],
        }


class InnerLoop:
    """Wraps a factory mode + evaluator. Optimizer calls loop.step()."""

    def __init__(
        self,
        project_dir: Path,
        mode: str = "evolve",
        evaluator: Evaluator | None = None,
        workflow: Workflow | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.factory_dir = self.project_dir / ".factory"
        self.mode = mode
        self.evaluator = evaluator
        self.workflow = workflow
        self.executor = executor or FactoryCeoExecutor(mode=mode, workflow=workflow)
        self._step_count = 0
        self._history: list[CycleRecord] = []

    def step(self, directives: dict[str, Any] | None = None) -> CycleRecord:
        """Run one inner-loop cycle and return structured results."""
        record = self.executor.execute(self.project_dir, directives)

        if self.evaluator and record.experiments:
            for exp in record.experiments:
                eval_files = [
                    Path(a) for a in exp.eval_artifacts
                    if a.endswith(".json") and "eval" in Path(a).name
                ]
                if eval_files:
                    eval_result = self.evaluator.parse_many(eval_files)
                    if eval_result.valid:
                        exp.score_after = eval_result.score

            last_eval_files = [
                Path(a) for exp in record.experiments
                for a in exp.eval_artifacts
                if a.endswith(".json") and "eval" in Path(a).name
            ]
            if last_eval_files:
                final = self.evaluator.parse(last_eval_files[-1])
                record.score_end = final.score

        record.cycle_number = self._step_count + 1
        self._step_count += 1
        self._history.append(record)
        return record

    def collect(self) -> CycleRecord:
        """Collect results without running a cycle. Useful after manual runs."""
        return self._collect_results()

    def score_trajectory(self) -> list[float]:
        """Score history across all steps."""
        if self._history:
            return [r.score_end for r in self._history if r.score_end is not None]
        analyzer = CycleAnalyzer(self.factory_dir, workflow=self.workflow)
        return analyzer.trajectory()

    def total_cost(self) -> float:
        """Cumulative cost across all steps."""
        return sum(r.total_cost_usd for r in self._history)

    def history(self) -> list[CycleRecord]:
        """All cycle records from this session."""
        return list(self._history)

    def _collect_results(self) -> CycleRecord:
        """Read execution artifacts + eval artifacts, compose into CycleRecord."""
        analyzer = CycleAnalyzer(self.factory_dir, workflow=self.workflow)
        record = analyzer.latest()
        if record is None:
            record = CycleRecord(
                cycle_number=0,
                mode=self.mode,
                started_at=None,
                ended_at=None,
                duration_s=0,
                score_start=None,
                score_end=None,
                score_delta=None,
            )

        if record.mode is None:
            record.mode = self.mode

        if self.evaluator and record.experiments:
            for exp in record.experiments:
                eval_files = [
                    Path(a) for a in exp.eval_artifacts
                    if a.endswith(".json") and "eval" in Path(a).name
                ]
                if eval_files:
                    eval_result = self.evaluator.parse_many(eval_files)
                    if eval_result.valid:
                        exp.score_after = eval_result.score

            last_eval_files = [
                Path(a) for exp in record.experiments
                for a in exp.eval_artifacts
                if a.endswith(".json") and "eval" in Path(a).name
            ]
            if last_eval_files:
                final = self.evaluator.parse(last_eval_files[-1])
                record.score_end = final.score

        return record

    def _write_directives(self, directives: dict[str, Any]) -> None:
        """Write outer-loop directives as a factory message (backward compat)."""
        msg_dir = self.factory_dir / "messages"
        msg_dir.mkdir(parents=True, exist_ok=True)
        msg_id = f"outer-loop-{self._step_count:04d}"
        msg_path = msg_dir / f"{msg_id}.md"

        lines = ["# Outer Loop Directives\n"]
        for key, value in directives.items():
            if isinstance(value, list):
                lines.append(f"- **{key}:** {', '.join(str(v) for v in value)}")
            else:
                lines.append(f"- **{key}:** {value}")

        msg_path.write_text("\n".join(lines) + "\n")
