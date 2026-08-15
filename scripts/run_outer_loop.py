#!/usr/bin/env python3
"""Run the full outer loop evolution pipeline for FeatureBench.

Steps:
1. Smoke test — 1 easy instance to verify the pipeline works
2. Calibration — 10 diverse instances to measure seed baseline
3. Evolution — 2 generations, population 4
4. Report — write results summary
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import structlog

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)
log = structlog.get_logger()

PROJECT_DIR = Path(__file__).resolve().parents[1]
FB_DIR = PROJECT_DIR / "featurebench" / "featurebench"
OUTER_LOOP_DIR = PROJECT_DIR / ".factory" / "outer_loop"
RESULTS_DIR = PROJECT_DIR / "results"

SMOKE_INSTANCE = "pypa__packaging.013f3b03.test_metadata.e00b5801.lv1"

CALIBRATION_INSTANCES = [
    "pydantic__pydantic.e1dcaf9e.test_deprecated_fields.40a2ec54.lv1",
    "fastapi__fastapi.02e108d1.test_compat.71e8518f.lv1",
    "pandas-dev__pandas.82fa2715.test_col.a592871d.lv1",
    "mwaskom__seaborn.7001ebe7.test_bar.123ed709.lv1",
    "sphinx-doc__sphinx.e347e59c.test_build_gettext.2721e644.lv1",
    "matplotlib__matplotlib.86a476d2.test_backend_registry.872ba384.lv1",
    "sympy__sympy.c1097516.test_inverse.c240ffe7.lv1",
    "mlflow__mlflow.93dab383.test_abstract_store.e5ff5123.lv1",
    "pytest-dev__pytest.68016f0e.test_local.40fb2f1f.lv1",
    "pypa__packaging.013f3b03.test_metadata.e00b5801.lv1",
]

AGENT_TIMEOUT = 600


def emit_progress(event: dict[str, object]) -> None:
    event["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    line = json.dumps(event, default=str)
    progress_path = OUTER_LOOP_DIR / "progress.jsonl"
    with progress_path.open("a") as f:
        f.write(line + "\n")
    log.info(event.get("event_type", "unknown"), **{k: v for k, v in event.items() if k not in ("event_type", "timestamp")})


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.rename(path)


def step_smoke_test() -> dict[str, object]:
    """STEP 2: Smoke test on 1 easy instance."""
    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow

    emit_progress({"event_type": "smoke_test_start", "instance": SMOKE_INSTANCE})

    seed_wf = create_seed_workflow()
    evaluator = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=AGENT_TIMEOUT,
    )

    start = time.monotonic()
    result = evaluator(seed_wf, str(PROJECT_DIR), [SMOKE_INSTANCE])
    elapsed = time.monotonic() - start

    smoke_result = {
        "instance": SMOKE_INSTANCE,
        "score": result.score,
        "resolved": result.score > 0,
        "elapsed_seconds": round(elapsed, 1),
        "details": result.details,
    }

    save_json(OUTER_LOOP_DIR / "smoke_test.json", smoke_result)
    emit_progress({
        "event_type": "smoke_test_complete",
        "score": result.score,
        "resolved": result.score > 0,
        "elapsed_seconds": round(elapsed, 1),
    })

    log.info(
        "smoke_test_result",
        instance=SMOKE_INSTANCE,
        score=result.score,
        resolved=result.score > 0,
        elapsed=f"{elapsed:.1f}s",
    )
    return smoke_result


def step_calibration() -> dict[str, object]:
    """STEP 3: Calibration on 10 diverse instances."""
    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow

    emit_progress({
        "event_type": "calibration_start",
        "instances": len(CALIBRATION_INSTANCES),
    })

    seed_wf = create_seed_workflow()
    evaluator = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=AGENT_TIMEOUT,
    )

    results: dict[str, object] = {}
    resolved_count = 0
    total_elapsed = 0.0

    for i, iid in enumerate(CALIBRATION_INSTANCES):
        emit_progress({
            "event_type": "calibration_instance_start",
            "instance": iid,
            "index": i + 1,
            "total": len(CALIBRATION_INSTANCES),
        })

        start = time.monotonic()
        try:
            ev = evaluator(seed_wf, str(PROJECT_DIR), [iid])
            elapsed = time.monotonic() - start
            resolved = ev.score > 0
            results[iid] = {
                "score": ev.score,
                "resolved": resolved,
                "elapsed_seconds": round(elapsed, 1),
                "details": ev.details,
            }
            if resolved:
                resolved_count += 1
        except Exception as exc:
            elapsed = time.monotonic() - start
            results[iid] = {
                "score": 0.0,
                "resolved": False,
                "elapsed_seconds": round(elapsed, 1),
                "error": str(exc),
            }

        total_elapsed += elapsed
        emit_progress({
            "event_type": "calibration_instance_complete",
            "instance": iid,
            "score": results[iid]["score"],
            "resolved": results[iid]["resolved"],
            "elapsed_seconds": round(elapsed, 1),
        })

        # Save incremental calibration results
        save_json(OUTER_LOOP_DIR / "calibration.json", {
            "instances": results,
            "resolved_so_far": resolved_count,
            "total_so_far": i + 1,
            "seed_score": resolved_count / (i + 1),
        })

    seed_score = resolved_count / max(len(CALIBRATION_INSTANCES), 1)

    # Split: resolved instances for holdout, rest for training
    # (training on failed instances gives more room for improvement)
    training = [iid for iid in CALIBRATION_INSTANCES if not results[iid].get("resolved", False)]
    holdout = [iid for iid in CALIBRATION_INSTANCES if results[iid].get("resolved", False)]

    # Ensure at least some training instances
    if len(training) < 3:
        training = CALIBRATION_INSTANCES[:7]
        holdout = CALIBRATION_INSTANCES[7:]

    calibration = {
        "instances": results,
        "training": training,
        "holdout": holdout,
        "total": len(CALIBRATION_INSTANCES),
        "seed_score": seed_score,
        "resolved_count": resolved_count,
        "total_elapsed_seconds": round(total_elapsed, 1),
    }

    save_json(OUTER_LOOP_DIR / "calibration.json", calibration)

    emit_progress({
        "event_type": "calibration_complete",
        "seed_score": seed_score,
        "resolved": resolved_count,
        "total": len(CALIBRATION_INSTANCES),
        "training": len(training),
        "holdout": len(holdout),
        "total_elapsed_seconds": round(total_elapsed, 1),
    })

    log.info(
        "calibration_done",
        seed_score=f"{seed_score:.2f}",
        resolved=resolved_count,
        total=len(CALIBRATION_INSTANCES),
        training=len(training),
        holdout=len(holdout),
    )
    return calibration


def step_evolution(calibration: dict[str, object]) -> dict[str, object]:
    """STEP 4: Evolution — 2 generations, population 4."""
    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.engine import SwarmEngine
    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow
    from factory.outer_loop.models import SwarmConfig
    from factory.outer_loop.progress import ProgressTracker

    training = calibration["training"]
    holdout = calibration["holdout"]

    emit_progress({
        "event_type": "evolution_start",
        "generations": 2,
        "population": 4,
        "budget": 20,
        "training": len(training),
        "holdout": len(holdout),
    })

    config = SwarmConfig(
        benchmark="featurebench",
        budget=20,
        population_size=4,
        training_instances=training,
        holdout_instances=holdout,
        parallelism=2,
        tournament_size=3,
    )

    direct_eval = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=1200,
    )
    evaluator = SwarmEvaluator(config=config, evaluator_fn=direct_eval)
    progress_tracker = ProgressTracker(OUTER_LOOP_DIR)
    engine = SwarmEngine(
        config=config,
        evaluator=evaluator,
        checkpoint_dir=OUTER_LOOP_DIR,
        progress_tracker=progress_tracker,
    )
    seed_wf = create_seed_workflow()

    start = time.monotonic()
    result = engine.run(seed_wf, str(PROJECT_DIR))
    elapsed = time.monotonic() - start

    result_data = result.model_dump(mode="json")
    result_data["elapsed_seconds"] = round(elapsed, 1)

    save_json(OUTER_LOOP_DIR / "evolution_results.json", result_data)

    emit_progress({
        "event_type": "evolution_complete",
        "best_score": result.best_score,
        "holdout_score": result.holdout_score,
        "generations": result.generations_completed,
        "evaluations": result.total_evaluations,
        "convergence": result.convergence_reason,
        "elapsed_seconds": round(elapsed, 1),
    })

    log.info(
        "evolution_done",
        best_score=f"{result.best_score:.3f}",
        holdout_score=f"{result.holdout_score:.3f}",
        generations=result.generations_completed,
        evaluations=result.total_evaluations,
        convergence=result.convergence_reason,
        elapsed=f"{elapsed:.0f}s",
    )
    return result_data


def step_report(
    smoke_result: dict[str, object],
    calibration: dict[str, object],
    evolution_result: dict[str, object],
) -> None:
    """STEP 5: Write results report."""
    report_lines = [
        "# Outer Loop v2 — FeatureBench Evolution Report",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "## Smoke Test",
        "",
        f"- **Instance:** `{smoke_result.get('instance', 'N/A')}`",
        f"- **Resolved:** {smoke_result.get('resolved', False)}",
        f"- **Score:** {smoke_result.get('score', 0.0)}",
        f"- **Elapsed:** {smoke_result.get('elapsed_seconds', 0)}s",
        "",
        "## Calibration (Seed Workflow Baseline)",
        "",
        f"- **Seed score:** {calibration.get('seed_score', 0.0):.2f}",
        f"- **Resolved:** {calibration.get('resolved_count', 0)} / {calibration.get('total', 0)}",
        f"- **Training set:** {len(calibration.get('training', []))} instances",
        f"- **Holdout set:** {len(calibration.get('holdout', []))} instances",
        f"- **Total elapsed:** {calibration.get('total_elapsed_seconds', 0)}s",
        "",
        "### Per-Instance Results",
        "",
        "| Instance | Score | Resolved | Time (s) |",
        "|----------|-------|----------|----------|",
    ]

    instances = calibration.get("instances", {})
    for iid, data in instances.items():
        if isinstance(data, dict):
            short_id = iid.split(".")[-2][:12] + "." + iid.split(".")[-1]
            score = data.get("score", 0.0)
            resolved = "Yes" if data.get("resolved", False) else "No"
            elapsed = data.get("elapsed_seconds", 0)
            report_lines.append(f"| `{short_id}` | {score:.2f} | {resolved} | {elapsed} |")

    report_lines.extend([
        "",
        "## Evolution",
        "",
        f"- **Best score:** {evolution_result.get('best_score', 0.0):.3f}",
        f"- **Holdout score:** {evolution_result.get('holdout_score', 0.0):.3f}",
        f"- **Overfit flag:** {evolution_result.get('overfit_flag', False)}",
        f"- **Generations:** {evolution_result.get('generations_completed', 0)}",
        f"- **Total evaluations:** {evolution_result.get('total_evaluations', 0)}",
        f"- **Convergence reason:** {evolution_result.get('convergence_reason', 'N/A')}",
        f"- **Total elapsed:** {evolution_result.get('elapsed_seconds', 0)}s",
        "",
    ])

    # Score trajectory
    trajectory = evolution_result.get("trajectory", [])
    if trajectory:
        report_lines.extend([
            "### Score Trajectory",
            "",
            "| Generation | Best Score | Mean Score | Diversity | Holdout |",
            "|------------|-----------|------------|-----------|---------|",
        ])
        for gen in trajectory:
            if isinstance(gen, dict):
                report_lines.append(
                    f"| {gen.get('generation', '?')} "
                    f"| {gen.get('best_score', 0):.3f} "
                    f"| {gen.get('mean_score', 0):.3f} "
                    f"| {gen.get('diversity', 0):.3f} "
                    f"| {gen.get('holdout_score', 0):.3f} |"
                )
        report_lines.append("")

    # Mutations
    hp_history = evolution_result.get("hyperparameter_history", [])
    if hp_history:
        report_lines.extend([
            "### Hyperparameter History",
            "",
            "| Generation | Mutation Rate | Novel Count | Pop Size |",
            "|------------|--------------|-------------|----------|",
        ])
        for hp in hp_history:
            if isinstance(hp, dict):
                report_lines.append(
                    f"| {hp.get('generation', '?')} "
                    f"| {hp.get('mutation_rate', 0):.3f} "
                    f"| {hp.get('novel_count', 0)} "
                    f"| {hp.get('population_size', 0)} |"
                )
        report_lines.append("")

    # Pareto front
    pareto = evolution_result.get("pareto_front", [])
    if pareto:
        report_lines.extend([
            "### Pareto Front",
            "",
            "| ID | Score | Generation | Complexity |",
            "|----|-------|------------|------------|",
        ])
        for ind in pareto:
            if isinstance(ind, dict):
                wf_data = ind.get("workflow_data", {})
                nodes = wf_data.get("nodes", {})
                report_lines.append(
                    f"| {ind.get('id', '?')[:12]} "
                    f"| {ind.get('score', 0):.3f} "
                    f"| {ind.get('generation', '?')} "
                    f"| {len(nodes)} nodes |"
                )
        report_lines.append("")

    # Summary
    seed_score = calibration.get("seed_score", 0.0)
    best_score = evolution_result.get("best_score", 0.0)
    improvement = best_score - seed_score if isinstance(seed_score, (int, float)) and isinstance(best_score, (int, float)) else 0

    report_lines.extend([
        "## Summary",
        "",
        f"- **Seed score:** {seed_score:.3f}",
        f"- **Evolved score:** {best_score:.3f}",
        f"- **Improvement:** {improvement:+.3f}",
        f"- **Archive size:** {evolution_result.get('archive_size', 0)}",
        "",
    ])

    report_text = "\n".join(report_lines)
    report_path = RESULTS_DIR / "outer_loop_v2_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text)
    log.info("report_written", path=str(report_path))


def main() -> int:
    OUTER_LOOP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    emit_progress({"event_type": "pipeline_start", "steps": ["smoke", "calibration", "evolution", "report"]})

    # STEP 2: Smoke test
    log.info("=" * 60)
    log.info("STEP 2: Smoke test on 1 easy instance")
    log.info("=" * 60)
    try:
        smoke_result = step_smoke_test()
    except Exception as exc:
        log.error("smoke_test_failed", error=str(exc))
        smoke_result = {"instance": SMOKE_INSTANCE, "score": 0.0, "resolved": False, "error": str(exc)}
        emit_progress({"event_type": "smoke_test_failed", "error": str(exc)})

    # STEP 3: Calibration
    log.info("=" * 60)
    log.info("STEP 3: Calibration on 10 instances")
    log.info("=" * 60)
    try:
        calibration = step_calibration()
    except Exception as exc:
        log.error("calibration_failed", error=str(exc))
        emit_progress({"event_type": "calibration_failed", "error": str(exc)})
        return 1

    # STEP 4: Evolution
    log.info("=" * 60)
    log.info("STEP 4: Evolution — 2 generations, population 4")
    log.info("=" * 60)
    try:
        evolution_result = step_evolution(calibration)
    except Exception as exc:
        log.error("evolution_failed", error=str(exc))
        evolution_result = {
            "best_score": 0.0,
            "holdout_score": 0.0,
            "generations_completed": 0,
            "total_evaluations": 0,
            "convergence_reason": f"error: {exc}",
            "error": str(exc),
        }
        emit_progress({"event_type": "evolution_failed", "error": str(exc)})

    # STEP 5: Report
    log.info("=" * 60)
    log.info("STEP 5: Write report")
    log.info("=" * 60)
    step_report(smoke_result, calibration, evolution_result)

    emit_progress({"event_type": "pipeline_complete"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
