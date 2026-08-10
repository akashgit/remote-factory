"""Composition parser and multi-mode executor for chained workflow execution."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import structlog
from pydantic import BaseModel, ConfigDict, Field

from factory.workflow.board import Board
from factory.workflow.executor import ExecutionResult, WorkflowExecutor
from factory.workflow.primitives import DEFAULT_AGENT_POOL, AgentConfig
from factory.workflow.registry import WorkflowRegistry

log = structlog.get_logger()

BUILTIN_MODES: frozenset[str] = frozenset({
    "discover",
    "review",
    "improve",
    "build",
    "research",
    "meta",
    "design",
    "refine",
    "create",
    "founder",
    "evolve",
    "spec-generate",
    "spec-update",
    "doc-generate",
    "doc-update",
    "parallel-improve",
    "frontend-design",
    "frontend-design-discover",
    "frontend-design-scan",
    "skill-refine",
})


class SequentialStep(BaseModel):
    """A single mode executed sequentially."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["sequential"] = "sequential"
    mode: str


class ParallelStep(BaseModel):
    """Multiple modes executed in parallel."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["parallel"] = "parallel"
    modes: list[str]


CompositionStep = Annotated[
    Union[SequentialStep, ParallelStep],
    Field(discriminator="type"),
]


def parse_mode_spec(spec: str) -> list[SequentialStep | ParallelStep]:
    """Parse a mode spec string into composition steps.

    Syntax: comma separates sequential stages, plus separates parallel modes
    within a stage. Example: ``"discover,a+b,improve"`` yields
    ``[Sequential(discover), Parallel([a, b]), Sequential(improve)]``.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Mode spec must not be empty")

    steps: list[SequentialStep | ParallelStep] = []
    stages = spec.split(",")

    for stage in stages:
        stage = stage.strip()
        if not stage:
            raise ValueError("Empty stage in mode spec (consecutive or trailing commas)")

        modes = [m.strip() for m in stage.split("+")]
        modes = [m for m in modes if m]

        if not modes:
            raise ValueError(f"Empty mode name in stage: {stage!r}")

        if len(modes) == 1:
            steps.append(SequentialStep(mode=modes[0]))
        else:
            steps.append(ParallelStep(modes=modes))

    return steps


def validate_composition(
    steps: list[SequentialStep | ParallelStep],
    registry_names: set[str] | None = None,
) -> list[str]:
    """Validate a parsed composition. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    if not steps:
        errors.append("Composition must have at least one step")
        return errors

    for i, step in enumerate(steps):
        if isinstance(step, SequentialStep):
            if not step.mode:
                errors.append(f"Step {i}: empty mode name")
            if registry_names is not None and step.mode not in registry_names:
                errors.append(f"Step {i}: unknown mode {step.mode!r}")
        elif isinstance(step, ParallelStep):
            if not step.modes:
                errors.append(f"Step {i}: parallel step has no modes")
            for mode in step.modes:
                if not mode:
                    errors.append(f"Step {i}: empty mode name in parallel step")
                if mode in BUILTIN_MODES:
                    errors.append(
                        f"Step {i}: built-in mode {mode!r} cannot appear in a ParallelStep"
                    )
                if registry_names is not None and mode not in registry_names:
                    errors.append(f"Step {i}: unknown mode {mode!r}")

    return errors


def validate_composition_with_contracts(
    steps: list[SequentialStep | ParallelStep],
    registry: WorkflowRegistry,
    project_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Validate composition using board data contracts from workflow definitions.

    Returns (errors, warnings) where:
    - errors: fatal issues (overlapping board_writes in parallel steps)
    - warnings: advisory issues (unsatisfied board_reads in sequential steps)
    """
    errors: list[str] = []
    warnings: list[str] = []

    def _get_workflow(mode: str) -> Any:
        return registry.get_workflow(mode, project_path)

    written_keys: set[str] = set()

    for i, step in enumerate(steps):
        if isinstance(step, ParallelStep):
            parallel_writes: dict[str, list[str]] = {}
            for mode in step.modes:
                if mode in BUILTIN_MODES:
                    errors.append(
                        f"Step {i}: built-in mode {mode!r} cannot appear in a ParallelStep"
                    )
                wf = _get_workflow(mode)
                if wf is None:
                    continue
                for key in wf.board_writes:
                    parallel_writes.setdefault(key, []).append(mode)

            for key, writers in parallel_writes.items():
                if len(writers) > 1:
                    errors.append(
                        f"Step {i}: overlapping board_writes key {key!r} "
                        f"in parallel modes {writers}"
                    )

            for mode in step.modes:
                wf = _get_workflow(mode)
                if wf is not None:
                    written_keys.update(wf.board_writes)

        elif isinstance(step, SequentialStep):
            wf = _get_workflow(step.mode)
            if wf is not None:
                for key in wf.board_reads:
                    if key not in written_keys:
                        warnings.append(
                            f"Step {i}: mode {step.mode!r} reads board key {key!r} "
                            f"not written by any preceding mode"
                        )
                written_keys.update(wf.board_writes)

    return errors, warnings


class MultiModeExecutor:
    """Walk a list of CompositionSteps, executing modes sequentially or in parallel."""

    def __init__(
        self,
        project_path: Path,
        board: Board,
        run_id: str,
        *,
        agent_pool: dict[str, AgentConfig] | None = None,
        dry_run: bool = False,
    ) -> None:
        self._project_path = project_path
        self._board = board
        self._run_id = run_id
        self._agent_pool = agent_pool or dict(DEFAULT_AGENT_POOL)
        self._dry_run = dry_run

    async def execute(self, steps: list[SequentialStep | ParallelStep]) -> dict[str, Any]:
        results: dict[str, Any] = {}

        for i, step in enumerate(steps):
            if isinstance(step, SequentialStep):
                log.info(
                    "compositor.step.start",
                    step_index=i,
                    step_type="sequential",
                    modes=[step.mode],
                )
                t0 = time.monotonic()
                result = await self._execute_single_mode(step.mode)
                elapsed = (time.monotonic() - t0) * 1000
                results[step.mode] = result

                log.info(
                    "compositor.step.complete",
                    step_index=i,
                    step_type="sequential",
                    modes=[step.mode],
                    duration_ms=round(elapsed, 1),
                )

                if result.halted:
                    log.warning(
                        "compositor.step.failed",
                        step_index=i,
                        step_type="sequential",
                        error=result.halt_reason,
                    )
                    break

            elif isinstance(step, ParallelStep):
                log.info(
                    "compositor.step.start",
                    step_index=i,
                    step_type="parallel",
                    modes=step.modes,
                )
                log.info("compositor.parallel.start", modes=step.modes)

                t0 = time.monotonic()
                parallel_results = await self._execute_parallel(step.modes)
                elapsed = (time.monotonic() - t0) * 1000

                completed = [m for m, r in parallel_results.items() if r.success]
                failed = [m for m, r in parallel_results.items() if not r.success]
                results.update(parallel_results)

                log.info(
                    "compositor.parallel.join",
                    completed_modes=completed,
                    failed_modes=failed,
                )
                log.info(
                    "compositor.step.complete",
                    step_index=i,
                    step_type="parallel",
                    modes=step.modes,
                    duration_ms=round(elapsed, 1),
                )

                if failed:
                    log.warning(
                        "compositor.step.failed",
                        step_index=i,
                        step_type="parallel",
                        error=f"Failed modes: {failed}",
                    )
                    break

                await self._board.async_write_global(
                    "parallel_results",
                    {m: {"success": r.success, "nodes_executed": r.nodes_executed} for m, r in parallel_results.items()},
                )

        await self._board.async_save()
        return results

    async def _execute_single_mode(self, mode: str) -> ExecutionResult:
        async with self._board._lock:
            self._board.state.current_mode = mode

        wf = WorkflowRegistry.get_workflow(mode, self._project_path)
        if wf is None:
            result = ExecutionResult()
            result.halted = True
            result.halt_reason = f"No workflow registered for mode {mode!r}"
            return result

        executor = WorkflowExecutor(
            wf,
            self._project_path,
            agent_pool=self._agent_pool,
            dry_run=self._dry_run,
            mode_prefix=mode,
        )
        try:
            result = await executor.execute()
        except Exception as exc:
            result = ExecutionResult()
            result.halted = True
            result.halt_reason = str(exc)
            log.error("compositor.mode.exception", mode=mode, error=str(exc))

        await self._board.async_write(mode, "result", {
            "success": result.success,
            "halted": result.halted,
            "halt_reason": result.halt_reason,
            "nodes_executed": result.nodes_executed,
            "duration_ms": result.duration_ms,
        })
        await self._board.async_mark_mode_complete(mode)

        return result

    async def _execute_parallel(self, modes: list[str]) -> dict[str, ExecutionResult]:
        results: dict[str, ExecutionResult] = {}
        tasks: dict[str, asyncio.Task[ExecutionResult]] = {}
        failed = asyncio.Event()

        async def _run_and_check(mode: str) -> ExecutionResult:
            result = await self._execute_single_mode(mode)
            if result.halted:
                failed.set()
            return result

        for mode in modes:
            task = asyncio.create_task(
                _run_and_check(mode),
                name=f"compositor-{mode}",
            )
            tasks[mode] = task

        waiter = asyncio.create_task(failed.wait())
        all_tasks = set(tasks.values())

        done, pending = await asyncio.wait(
            all_tasks | {waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if failed.is_set():
            for t in tasks.values():
                if not t.done():
                    t.cancel()
            if any(not t.done() for t in tasks.values()):
                await asyncio.wait(tasks.values(), timeout=5.0)
        else:
            waiter.cancel()
            if any(not t.done() for t in tasks.values()):
                await asyncio.wait(tasks.values())

        for mode, task in tasks.items():
            if task.done() and not task.cancelled():
                try:
                    results[mode] = task.result()
                except Exception as exc:
                    r = ExecutionResult()
                    r.halted = True
                    r.halt_reason = str(exc)
                    results[mode] = r
            else:
                r = ExecutionResult()
                r.halted = True
                r.halt_reason = "Cancelled due to sibling failure"
                results[mode] = r

        return results
