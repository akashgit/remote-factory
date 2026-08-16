#!/usr/bin/env python3
"""Run evolution with a mixed lv1+lv2 calibration for ~50% variance.

lv1 instances are too easy (100%), lv2 are too hard (0%). Mix them
to get the ~50% pass rate needed for meaningful evolutionary signal.
Uses pre-computed calibration data — no re-evaluation needed.
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

# Pick 4 lv1 (fast, builder passes) + 3 lv2 (builder fails) for training
# Pick 2 lv1 + 1 lv2 for holdout
# This gives ~57% seed pass rate on training, ~67% on holdout
TRAINING = [
    "fastapi__fastapi.02e108d1.test_compat.71e8518f.lv1",      # lv1 PASS
    "sympy__sympy.c1097516.test_inverse.c240ffe7.lv1",          # lv1 PASS
    "pypa__packaging.013f3b03.test_metadata.e00b5801.lv1",      # lv1 PASS
    "mlflow__mlflow.93dab383.test_abstract_store.e5ff5123.lv1",  # lv1 PASS
    "fastapi__fastapi.02e108d1.test_compat.71e8518f.lv2",       # lv2 FAIL
    "mlflow__mlflow.93dab383.test_config.c63d41b0.lv2",         # lv2 FAIL
    "mwaskom__seaborn.7001ebe7.test_regression.ce8c62e2.lv2",   # lv2 FAIL
]

HOLDOUT = [
    "pytest-dev__pytest.68016f0e.raises_group.c28bf36a.lv1",    # lv1 PASS
    "matplotlib__matplotlib.86a476d2.test_backend_registry.872ba384.lv1",  # lv1 PASS
    "pandas-dev__pandas.82fa2715.test_col.a592871d.lv2",        # lv2 FAIL
]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run evolution with mixed lv1+lv2 instances")
    parser.add_argument("--generations", type=int, default=2, help="Max generations")
    parser.add_argument("--population", type=int, default=4, help="Population size")
    parser.add_argument("--budget", type=int, default=20, help="Evaluation budget")
    parser.add_argument("--parallelism", type=int, default=1, help="Parallel evaluations")
    parser.add_argument("--timeout", type=int, default=600, help="Per-agent timeout")
    args = parser.parse_args()

    # Verify all instances exist
    all_instances = TRAINING + HOLDOUT
    missing = [i for i in all_instances if not (FB_DIR / i).exists()]
    if missing:
        print(f"Missing instances: {missing}", file=sys.stderr)
        return 1

    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.engine import SwarmEngine
    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow
    from factory.outer_loop.models import SwarmConfig
    from factory.outer_loop.progress import ProgressTracker

    # Write mixed calibration
    cal_mixed = {
        "training": TRAINING,
        "holdout": HOLDOUT,
        "total": len(all_instances),
        "seed_score": 4 / 7,  # 4 lv1 PASS out of 7 training
        "seed_name": "featurebench-builder-only",
        "level": "mixed (lv1+lv2)",
        "note": "4 lv1 (PASS) + 3 lv2 (FAIL) training, 2 lv1 + 1 lv2 holdout",
    }
    cal_out = OUTER_LOOP_DIR / "calibration_mixed.json"
    cal_out.write_text(json.dumps(cal_mixed, indent=2))

    config = SwarmConfig(
        benchmark="featurebench",
        budget=args.budget,
        population_size=args.population,
        training_instances=TRAINING,
        holdout_instances=HOLDOUT,
        parallelism=args.parallelism,
        tournament_size=min(3, args.population),
    )

    direct_eval = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=args.timeout,
    )
    evaluator = SwarmEvaluator(config=config, evaluator_fn=direct_eval)
    progress = ProgressTracker(OUTER_LOOP_DIR)
    engine = SwarmEngine(
        config=config,
        evaluator=evaluator,
        checkpoint_dir=OUTER_LOOP_DIR,
        progress_tracker=progress,
    )
    seed_wf = create_seed_workflow(minimal=True)

    print("=" * 60)
    print("Outer Loop Evolution — Mixed lv1+lv2")
    print("=" * 60)
    print(f"Seed:        {seed_wf.name} ({len(seed_wf.nodes)} node)")
    print(f"Training:    {len(TRAINING)} (4 lv1 PASS + 3 lv2 FAIL)")
    print(f"Holdout:     {len(HOLDOUT)} (2 lv1 PASS + 1 lv2 FAIL)")
    print(f"Expected seed score: {4/7:.1%}")
    print(f"Population:  {args.population}")
    print(f"Budget:      {args.budget}")
    print(f"Generations: {args.generations}")
    print(f"Timeout:     {args.timeout}s per agent")
    print()

    start = time.monotonic()
    result = engine.run(seed_wf, str(PROJECT_DIR))
    elapsed = time.monotonic() - start

    result_data = result.model_dump(mode="json")
    result_data["elapsed_seconds"] = round(elapsed, 1)
    result_data["seed_type"] = "builder-only (minimal)"
    result_data["seed_name"] = seed_wf.name
    result_data["instance_mix"] = "lv1+lv2"

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
