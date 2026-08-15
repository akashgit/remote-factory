#!/usr/bin/env python3
"""Run the evolutionary search using calibration data.

Reads calibration.json for training/holdout split, then runs SwarmEngine.
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


def main() -> int:
    cal_path = OUTER_LOOP_DIR / "calibration.json"
    if not cal_path.exists():
        print(f"No calibration found at {cal_path}. Run calibration first.", file=sys.stderr)
        return 1

    calibration = json.loads(cal_path.read_text())
    training = calibration.get("training", [])
    holdout = calibration.get("holdout", [])

    if not training:
        print("No training instances in calibration.", file=sys.stderr)
        return 1

    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.engine import SwarmEngine
    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow
    from factory.outer_loop.models import SwarmConfig
    from factory.outer_loop.progress import ProgressTracker

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

    print("=" * 60)
    print("Outer Loop Evolution — FeatureBench")
    print("=" * 60)
    print(f"Training:    {len(training)} instances")
    print(f"Holdout:     {len(holdout)} instances")
    print(f"Population:  4")
    print(f"Budget:      20 evaluations")
    print(f"Parallelism: 2")
    print()

    start = time.monotonic()
    result = engine.run(seed_wf, str(PROJECT_DIR))
    elapsed = time.monotonic() - start

    result_data = result.model_dump(mode="json")
    result_data["elapsed_seconds"] = round(elapsed, 1)

    out = OUTER_LOOP_DIR / "evolution_results.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result_data, indent=2, default=str))
    tmp.rename(out)

    print()
    print("=" * 60)
    print(f"Best score:     {result.best_score:.3f}")
    print(f"Holdout score:  {result.holdout_score:.3f}")
    print(f"Overfit flag:   {result.overfit_flag}")
    print(f"Generations:    {result.generations_completed}")
    print(f"Evaluations:    {result.total_evaluations}")
    print(f"Convergence:    {result.convergence_reason}")
    print(f"Archive size:   {result.archive_size}")
    print(f"Elapsed:        {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"Results:        {out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
