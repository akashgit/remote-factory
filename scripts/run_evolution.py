#!/usr/bin/env python3
"""Run the evolutionary search using calibration data.

Reads calibration.json for training/holdout split, then runs SwarmEngine.
Supports --minimal flag to use builder-only seed workflow.
"""

from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description="Run outer-loop evolution on FeatureBench")
    parser.add_argument("--minimal", action="store_true", help="Use builder-only seed (no researcher/health_checker)")
    parser.add_argument("--generations", type=int, default=2, help="Max generations")
    parser.add_argument("--population", type=int, default=4, help="Population size")
    parser.add_argument("--budget", type=int, default=20, help="Total evaluation budget")
    parser.add_argument("--parallelism", type=int, default=2, help="Parallel evaluations")
    parser.add_argument("--timeout", type=int, default=600, help="Per-agent timeout in seconds")
    parser.add_argument("--calibration", default="calibration.json", help="Calibration file name")
    args = parser.parse_args()

    cal_path = OUTER_LOOP_DIR / args.calibration
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
        budget=args.budget,
        population_size=args.population,
        training_instances=training,
        holdout_instances=holdout,
        parallelism=args.parallelism,
        tournament_size=min(3, args.population),
    )

    direct_eval = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=args.timeout,
    )
    evaluator = SwarmEvaluator(config=config, evaluator_fn=direct_eval)
    progress_tracker = ProgressTracker(OUTER_LOOP_DIR)
    engine = SwarmEngine(
        config=config,
        evaluator=evaluator,
        checkpoint_dir=OUTER_LOOP_DIR,
        progress_tracker=progress_tracker,
    )
    seed_wf = create_seed_workflow(minimal=args.minimal)

    seed_type = "builder-only (minimal)" if args.minimal else "full 4-node pipeline"
    print("=" * 60)
    print("Outer Loop Evolution — FeatureBench")
    print("=" * 60)
    print(f"Seed:        {seed_wf.name} ({len(seed_wf.nodes)} node(s)) — {seed_type}")
    print(f"Training:    {len(training)} instances")
    print(f"Holdout:     {len(holdout)} instances")
    print(f"Population:  {args.population}")
    print(f"Budget:      {args.budget} evaluations")
    print(f"Generations: {args.generations}")
    print(f"Parallelism: {args.parallelism}")
    print(f"Timeout:     {args.timeout}s per agent")
    print()

    start = time.monotonic()
    result = engine.run(seed_wf, str(PROJECT_DIR))
    elapsed = time.monotonic() - start

    result_data = result.model_dump(mode="json")
    result_data["elapsed_seconds"] = round(elapsed, 1)
    result_data["seed_type"] = seed_type
    result_data["seed_name"] = seed_wf.name

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
