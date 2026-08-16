"""Fitness evaluation for workflow candidates in the evolutionary search.

Supports both legacy EvaluatorFn protocol (DirectFeatureBenchEvaluator) and
InnerLoop-based evaluation (FeatureBenchInnerLoop). CycleRecordCache provides
content-addressable caching keyed by workflow hash.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

from factory.cycle_analyzer import CycleRecord
from factory.outer_loop.models import EvalResult, SwarmConfig
from factory.outer_loop.similarity import structural_hash
from factory.workflow.primitives import Workflow

log = structlog.get_logger()


class FitnessCache:
    """Cache evaluation results keyed by (structural_hash, frozenset(instances))."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, frozenset[str]], tuple[float, float, float]] = {}

    def get(
        self, workflow: Workflow, instances: list[str]
    ) -> tuple[float, float, float] | None:
        key = (structural_hash(workflow), frozenset(instances))
        return self._cache.get(key)

    def put(
        self, workflow: Workflow, instances: list[str], score: float, cost: float
    ) -> None:
        key = (structural_hash(workflow), frozenset(instances))
        self._cache[key] = (score, cost, time.time())

    @property
    def size(self) -> int:
        return len(self._cache)


class CycleRecordCache:
    """Cache CycleRecords keyed by workflow content hash.

    Content-addressable via sha256(workflow.to_dict()).
    """

    def __init__(self) -> None:
        self._cache: dict[str, CycleRecord] = {}

    @staticmethod
    def workflow_hash(workflow: Workflow) -> str:
        blob = json.dumps(workflow.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, workflow: Workflow) -> CycleRecord | None:
        key = self.workflow_hash(workflow)
        return self._cache.get(key)

    def put(self, workflow: Workflow, record: CycleRecord) -> None:
        key = self.workflow_hash(workflow)
        self._cache[key] = record

    @property
    def size(self) -> int:
        return len(self._cache)


@runtime_checkable
class EvaluatorFn(Protocol):
    """Protocol for pluggable evaluation functions."""

    def __call__(
        self, workflow: Workflow, project_dir: str, instances: list[str]
    ) -> EvalResult: ...


class SwarmEvaluator:
    """Evaluates workflow candidates against benchmark instances.

    Supports both legacy EvaluatorFn and InnerLoop-based evaluation.
    When inner_loop_factory is provided, it takes precedence.
    """

    def __init__(
        self,
        config: SwarmConfig,
        evaluator_fn: EvaluatorFn | None = None,
        inner_loop_factory: Any | None = None,
    ) -> None:
        self._config = config
        self._evaluator_fn = evaluator_fn
        self._inner_loop_factory = inner_loop_factory
        self._cache = FitnessCache()
        self._cycle_cache = CycleRecordCache()
        self._cycle_records: dict[str, CycleRecord] = {}

    @property
    def cache(self) -> FitnessCache:
        return self._cache

    @property
    def cycle_cache(self) -> CycleRecordCache:
        return self._cycle_cache

    def get_cycle_record(self, individual_id: str) -> CycleRecord | None:
        return self._cycle_records.get(individual_id)

    def evaluate(
        self,
        workflow: Workflow,
        project_dir: str,
        instances: list[str],
        individual_id: str | None = None,
    ) -> EvalResult:
        """Evaluate a workflow on the given instances, using cache if available."""
        cached = self._cache.get(workflow, instances)
        if cached is not None:
            score, cost, _ = cached
            log.info("fitness_cache_hit", score=score)
            return EvalResult(score=score, cost_usd=cost, benchmark_score=score)

        if not self._check_mandatory_components(workflow):
            log.warning("mandatory_component_missing", workflow=workflow.name)
            return EvalResult(score=0.0, details={"rejected": "mandatory_component_missing"})

        if not self._check_frozen_nodes(workflow):
            log.warning("frozen_node_violated", workflow=workflow.name)
            return EvalResult(score=0.0, details={"rejected": "frozen_node_violated"})

        if self._inner_loop_factory is not None:
            return self._evaluate_via_inner_loop(
                workflow, project_dir, instances, individual_id
            )

        if self._evaluator_fn is not None:
            result = self._evaluator_fn(workflow, project_dir, instances)
        else:
            result = EvalResult(score=0.0, details={"note": "no_evaluator_fn_configured"})

        composite = self._compute_composite(result)
        result = result.model_copy(update={"score": composite})

        self._cache.put(workflow, instances, composite, result.cost_usd)
        return result

    def _evaluate_via_inner_loop(
        self,
        workflow: Workflow,
        project_dir: str,
        instances: list[str],
        individual_id: str | None = None,
    ) -> EvalResult:
        """Evaluate using InnerLoop.step() for rich CycleRecord exhaust."""
        from factory.outer_loop.featurebench_inner_loop import FeatureBenchInnerLoop

        cached_record = self._cycle_cache.get(workflow)
        if cached_record is not None:
            score = cached_record.score_end or 0.0
            cost = cached_record.total_cost_usd
            log.info("cycle_record_cache_hit", score=score)
            if individual_id:
                self._cycle_records[individual_id] = cached_record
            return EvalResult(score=score, cost_usd=cost, benchmark_score=score)

        try:
            mode_name = self._inner_loop_factory(workflow) if callable(self._inner_loop_factory) else "evolve"
            loop = FeatureBenchInnerLoop(
                project_dir=Path(project_dir),
                mode=mode_name,
                workflow=workflow,
                frozen_nodes=frozenset(self._config.frozen_node_ids),
            )
            record = loop.step()

            score = record.score_end or 0.0
            cost = record.total_cost_usd

            self._cycle_cache.put(workflow, record)
            if individual_id:
                self._cycle_records[individual_id] = record

            num_nodes = len(workflow.nodes)
            parsimony = 0.01 * num_nodes
            composite = max(0.0, score - parsimony)

            self._cache.put(workflow, instances, composite, cost)

            return EvalResult(
                score=composite,
                benchmark_score=score,
                cost_usd=cost,
                complexity=float(num_nodes),
                details={
                    "experiments": len(record.experiments),
                    "steps": len(record.steps),
                    "kept": record.kept,
                    "reverted": record.reverted,
                    "parsimony_penalty": parsimony,
                },
            )
        except Exception as exc:
            log.error("inner_loop_eval_failed", error=str(exc), exc_info=True)
            return EvalResult(
                score=0.0,
                details={"error": str(exc), "evaluation_method": "inner_loop"},
            )

    def evaluate_batch(
        self,
        workflows: list[Workflow],
        project_dir: str,
        instances: list[str],
        parallelism: int = 1,
    ) -> list[EvalResult]:
        return [self.evaluate(wf, project_dir, instances) for wf in workflows]

    def _compute_composite(self, result: EvalResult) -> float:
        norm_cost = min(result.cost_usd / 10.0, 1.0) if result.cost_usd > 0 else 0.0
        norm_complexity = min(result.complexity / 20.0, 1.0) if result.complexity > 0 else 0.0
        return (
            0.6 * result.benchmark_score
            + 0.2 * result.hygiene_score
            + 0.1 * (1.0 - norm_cost)
            + 0.1 * (1.0 - norm_complexity)
        )

    def _check_mandatory_components(self, workflow: Workflow) -> bool:
        if not self._config.mandatory_node_roles:
            return True
        present_roles: set[str] = set()
        for node in workflow.nodes.values():
            if hasattr(node, "role"):
                present_roles.add(node.role.value if hasattr(node.role, "value") else str(node.role))
        for role in self._config.mandatory_node_roles:
            if role not in present_roles:
                return False
        return True

    def _check_frozen_nodes(self, workflow: Workflow) -> bool:
        for fid in self._config.frozen_node_ids:
            if fid not in workflow.nodes:
                return False
        return True
