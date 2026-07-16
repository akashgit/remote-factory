"""Full cycle orchestrator — ROLLOUT → REFLECT → AGGREGATE → SELECT → UPDATE → EVALUATE."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

from factory.skillopt.aggregate import aggregate_reflections
from factory.skillopt.models import CycleResult
from factory.skillopt.reflect import _read_prompt_template, reflect_all
from factory.skillopt.select import select_edits
from factory.skillopt.update import apply_edits_to_workflow, revert_workflow

log = structlog.get_logger()


def _compute_score(results_dir: str) -> float:
    """Compute resolution rate from benchmark result JSON files."""
    results_path = Path(results_dir)
    if not results_path.is_dir():
        return 0.0
    files = list(results_path.glob("*.json"))
    if not files:
        return 0.0
    resolved = 0
    total = 0
    for f in files:
        try:
            data = json.loads(f.read_text())
            total += 1
            if data.get("resolved", False):
                resolved += 1
        except (json.JSONDecodeError, OSError):
            continue
    return resolved / total if total > 0 else 0.0


def _run_benchmark(benchmark: str) -> int:
    """Run a benchmark via run-harbor.sh and return the exit code."""
    script = Path(__file__).resolve().parents[2] / "benchmarks" / "run-harbor.sh"
    if not script.exists():
        log.error("run-harbor.sh not found", path=str(script))
        return 1
    try:
        result = subprocess.run(
            [str(script), benchmark, "--all"],
            capture_output=True,
            text=True,
            timeout=7200,
        )
        log.info(
            "benchmark run finished",
            benchmark=benchmark,
            returncode=result.returncode,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        log.error("benchmark timed out", benchmark=benchmark)
        return 1
    except (FileNotFoundError, OSError) as exc:
        log.error("benchmark run failed", error=str(exc))
        return 1


def run_cycle(
    benchmark: str,
    workflow_file: str,
    node_id: str,
    results_dir: str,
    learning_rate: int = 3,
    cycle_id: int = 1,
    skip_rollout: bool = False,
) -> CycleResult:
    """Run one full SkillOpt optimization cycle.

    Stages:
      1. Score the current results (baseline)
      2. Reflect on each trace
      3. Aggregate reflections into edit proposals
      4. Select top-K edits (textual learning rate)
      5. Apply edits to workflow file
      6. Re-run benchmark and compare scores
      7. Accept or revert

    Args:
        benchmark: Benchmark name (e.g. "swebench").
        workflow_file: Path to workflow .py file.
        node_id: AgentNode id whose prompt_template to optimize.
        results_dir: Path to directory of benchmark result JSON files.
        learning_rate: Max edits per cycle (textual learning rate).
        cycle_id: Cycle number for tracking.
        skip_rollout: If True, skip the re-evaluation benchmark run
            (useful for dry-run / structural testing).

    Returns:
        CycleResult with before/after scores and applied edits.
    """
    log.info(
        "starting SkillOpt cycle",
        cycle_id=cycle_id,
        benchmark=benchmark,
        node_id=node_id,
    )

    # ── 1. Baseline score ──
    score_before = _compute_score(results_dir)
    log.info("baseline score", score=score_before)

    # ── 2. Reflect ──
    reflections = reflect_all(results_dir, workflow_file, node_id)
    if not reflections:
        log.warning("no reflections produced — aborting cycle")
        return CycleResult(
            cycle_id=cycle_id,
            benchmark=benchmark,
            workflow_file=workflow_file,
            score_before=score_before,
            score_after=None,
            accepted=False,
            reflections=[],
            proposals_considered=[],
            proposals_applied=[],
        )

    # ── 3. Aggregate ──
    prompt_template = _read_prompt_template(workflow_file, node_id) or ""
    proposals = aggregate_reflections(reflections, prompt_template)

    # ── 4. Select ──
    selected = select_edits(
        proposals,
        learning_rate=learning_rate,
        prompt_template_length=len(prompt_template),
    )

    if not selected:
        log.warning("no edits selected — aborting cycle")
        return CycleResult(
            cycle_id=cycle_id,
            benchmark=benchmark,
            workflow_file=workflow_file,
            score_before=score_before,
            score_after=None,
            accepted=False,
            reflections=reflections,
            proposals_considered=proposals,
            proposals_applied=[],
        )

    # ── 5. Update ──
    apply_edits_to_workflow(workflow_file, node_id, selected)

    # ── 6. Evaluate ──
    score_after: float | None = None
    if not skip_rollout:
        rc = _run_benchmark(benchmark)
        if rc == 0:
            score_after = _compute_score(results_dir)
            log.info("post-edit score", score=score_after)
        else:
            log.warning("benchmark re-run failed — reverting")
            revert_workflow(workflow_file)
            return CycleResult(
                cycle_id=cycle_id,
                benchmark=benchmark,
                workflow_file=workflow_file,
                score_before=score_before,
                score_after=None,
                accepted=False,
                reflections=reflections,
                proposals_considered=proposals,
                proposals_applied=selected,
            )
    else:
        log.info("skipping rollout (dry run)")

    # ── 7. Accept or revert ──
    accepted = False
    if score_after is not None and score_after > score_before:
        accepted = True
        log.info(
            "edits ACCEPTED",
            improvement=round(score_after - score_before, 4),
        )
    elif score_after is not None:
        revert_workflow(workflow_file)
        log.info(
            "edits REJECTED — reverting",
            score_before=score_before,
            score_after=score_after,
        )
    else:
        log.info("no score comparison available (dry run or failed rollout)")

    return CycleResult(
        cycle_id=cycle_id,
        benchmark=benchmark,
        workflow_file=workflow_file,
        score_before=score_before,
        score_after=score_after,
        accepted=accepted,
        reflections=reflections,
        proposals_considered=proposals,
        proposals_applied=selected,
    )
