"""Fitness evaluation for workflow candidates in the evolutionary search."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol, runtime_checkable

import structlog

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


@runtime_checkable
class EvaluatorFn(Protocol):
    """Protocol for pluggable evaluation functions.

    For v1, this is a simple callable. Phase 4 will wire in InnerLoop.
    """

    def __call__(
        self, workflow: Workflow, project_dir: str, instances: list[str]
    ) -> EvalResult: ...


class SwarmEvaluator:
    """Evaluates workflow candidates against benchmark instances."""

    def __init__(
        self,
        config: SwarmConfig,
        evaluator_fn: EvaluatorFn | None = None,
    ) -> None:
        self._config = config
        self._evaluator_fn = evaluator_fn
        self._cache = FitnessCache()

    @property
    def cache(self) -> FitnessCache:
        return self._cache

    def evaluate(
        self,
        workflow: Workflow,
        project_dir: str,
        instances: list[str],
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

        if self._evaluator_fn is not None:
            result = self._evaluator_fn(workflow, project_dir, instances)
        else:
            result = EvalResult(score=0.0, details={"note": "no_evaluator_fn_configured"})

        score = result.benchmark_score
        result = result.model_copy(update={"score": score})

        self._cache.put(workflow, instances, score, result.cost_usd)
        return result

    def evaluate_batch(
        self,
        workflows: list[Workflow],
        project_dir: str,
        instances: list[str],
        parallelism: int = 1,
    ) -> list[EvalResult]:
        """Evaluate multiple workflows, optionally in parallel."""
        if parallelism <= 1 or len(workflows) <= 1:
            return [self.evaluate(wf, project_dir, instances) for wf in workflows]

        results: list[EvalResult | None] = [None] * len(workflows)
        # ThreadPoolExecutor is correct — Docker subprocess calls are I/O-bound (3.2s threads vs 3.4s processes).
        with ThreadPoolExecutor(max_workers=min(parallelism, len(workflows))) as executor:
            future_to_idx = {
                executor.submit(self.evaluate, wf, project_dir, instances): i
                for i, wf in enumerate(workflows)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception:
                    log.error("parallel_eval_failed", index=idx, exc_info=True)
                    results[idx] = EvalResult(score=0.0, details={"error": "parallel_eval_exception"})

        return [r if r is not None else EvalResult(score=0.0) for r in results]

    def _check_mandatory_components(self, workflow: Workflow) -> bool:
        """Verify workflow contains all mandatory node roles."""
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
        """Verify no frozen node was removed from the workflow."""
        for fid in self._config.frozen_node_ids:
            if fid not in workflow.nodes:
                return False
        return True
