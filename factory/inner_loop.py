"""InnerLoop — model-like wrapper for mode + evaluator that an outer-loop optimizer calls.

CycleAnalyzer handles execution tracing (what agents ran, costs, verdicts).
Evaluator handles score interpretation (parses evaluator-specific output artifacts).
InnerLoop composes both.

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
import time
import warnings
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
    """Wraps a factory mode + evaluator. Optimizer calls loop.step().

    frozen_nodes declares which workflow nodes are immutable during outer-loop
    optimization. Node-only: edges remain mutable. Orthogonal to file-level
    mutable_surfaces/fixed_surfaces in FactoryConfig. The outer loop is
    responsible for checking is_mutable() before modifying nodes.
    """

    def __init__(
        self,
        project_dir: Path,
        mode: str = "evolve",
        evaluator: Evaluator | None = None,
        workflow: Workflow | None = None,
        frozen_nodes: frozenset[str] = frozenset(),
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.factory_dir = self.project_dir / ".factory"
        self.mode = mode
        self.evaluator = evaluator
        self.workflow = workflow
        self.frozen_nodes = frozenset(frozen_nodes)
        self._step_count = 0
        self._history: list[CycleRecord] = []
        self._validate_frozen_nodes()

    def _validate_frozen_nodes(self) -> None:
        if not self.frozen_nodes or self.workflow is None:
            return
        invalid = self.frozen_nodes - self.workflow.nodes.keys()
        if invalid:
            raise ValueError(
                f"frozen_nodes contains IDs not in workflow.nodes: {sorted(invalid)}"
            )
        if len(self.frozen_nodes) == len(self.workflow.nodes):
            warnings.warn(
                "All nodes are frozen — outer loop has no mutable surface",
                stacklevel=3,
            )

    def is_mutable(self, node_id: str) -> bool:
        """Return True if node can be modified by the outer loop."""
        if self.workflow is None:
            return True
        if node_id not in self.workflow.nodes:
            raise ValueError(f"Unknown node ID: {node_id!r}")
        return node_id not in self.frozen_nodes

    def mutable_nodes(self) -> set[str]:
        """Return the set of node IDs the outer loop may modify."""
        if self.workflow is None:
            return set()
        return set(self.workflow.nodes.keys()) - self.frozen_nodes

    def immutable_nodes(self) -> set[str]:
        """Return the set of frozen node IDs."""
        return set(self.frozen_nodes)

    @staticmethod
    def _count_lines(path: Path) -> int:
        if not path.exists():
            return 0
        return len(path.read_text().splitlines())

    @staticmethod
    def _count_tsv_data_rows(path: Path) -> int:
        if not path.exists():
            return 0
        lines = path.read_text().splitlines()
        return max(0, len(lines) - 1)

    def step(self, directives: dict[str, Any] | None = None) -> CycleRecord:
        """Run one inner-loop cycle and return structured results.

        1. Write directives (steering from outer loop) if provided
        2. Snapshot artifact offsets for isolation
        3. Run the factory mode via subprocess
        4. Write cycle_summary.json with observable outcomes
        5. CycleAnalyzer reads only new execution artifacts (scoped by offset)
        6. Evaluator parses eval-specific artifacts (scores, metrics)
        7. Return composed CycleRecord
        """
        if directives:
            self._write_directives(directives)

        event_offset = self._count_lines(self.factory_dir / "events.jsonl")
        tsv_offset = self._count_tsv_data_rows(self.factory_dir / "results.tsv")

        head_before = self._get_git_head()
        t0 = time.monotonic()

        result = subprocess.run(
            [sys.executable, "-m", "factory", "ceo", str(self.project_dir),
             "--mode", self.mode, "--headless", "--no-worktree"],
            cwd=self.project_dir,
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        head_after = self._get_git_head()
        builder_committed = (
            head_before is not None
            and head_after is not None
            and head_before != head_after
        )

        record = self._collect_results(
            event_offset=event_offset, tsv_offset=tsv_offset,
        )

        self._write_cycle_summary(
            returncode=result.returncode,
            event_offset=event_offset,
            duration_ms=duration_ms,
            builder_committed=builder_committed,
            experiments=len(record.experiments),
        )

        if result.returncode != 0:
            record.errored = (record.errored or 0) + 1
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

    def _collect_results(
        self,
        event_offset: int = 0,
        tsv_offset: int = 0,
    ) -> CycleRecord:
        """Read execution artifacts + eval artifacts, compose into CycleRecord."""
        analyzer = CycleAnalyzer(
            self.factory_dir,
            workflow=self.workflow,
            event_offset=event_offset,
            tsv_offset=tsv_offset,
        )
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

        record.frozen_nodes = sorted(self.frozen_nodes)
        record.mutable_node_ids = sorted(self.mutable_nodes())

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

    def _get_git_head(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def _write_cycle_summary(
        self,
        returncode: int,
        event_offset: int,
        duration_ms: int,
        builder_committed: bool,
        experiments: int,
    ) -> Path:
        """Write a structured summary of observable outcomes from this cycle."""
        events_path = self.factory_dir / "events.jsonl"

        agents_spawned = 0
        agents_succeeded = 0
        agents_failed = 0
        total_cost = 0.0

        if events_path.exists():
            for idx, line in enumerate(events_path.read_text().splitlines()):
                if idx < event_offset:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                etype = e.get("type", "")
                if etype == "agent.started":
                    agents_spawned += 1
                elif etype == "agent.completed":
                    agents_succeeded += 1
                    total_cost += e.get("data", {}).get("total_cost_usd", 0) or 0
                elif etype == "agent.failed":
                    agents_failed += 1

        score = 0.0
        if agents_spawned > 0:
            score += 0.2
        if builder_committed:
            score += 0.2
        if returncode == 0:
            score += 0.2
        if agents_failed == 0 and agents_spawned > 0:
            score += 0.2
        if returncode == 0:
            score += 0.2

        errors: list[str] = []
        if returncode != 0:
            errors.append(f"subprocess exited with code {returncode}")

        summary = {
            "mode": self.mode,
            "score": round(score, 2),
            "cost_usd": round(total_cost, 2),
            "agents_spawned": agents_spawned,
            "agents_succeeded": agents_succeeded,
            "agents_failed": agents_failed,
            "builder_committed": builder_committed,
            "tests_passed": returncode == 0,
            "experiments": experiments,
            "duration_ms": duration_ms,
            "errors": errors,
        }

        summary_dir = self.factory_dir / "outer_loop" / "runs" / self.mode
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / "cycle_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")

        return summary_path

    def _write_directives(self, directives: dict[str, Any]) -> None:
        """Write outer-loop directives as a factory message."""
        if self.frozen_nodes:
            directives['frozen_nodes'] = sorted(self.frozen_nodes)
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
