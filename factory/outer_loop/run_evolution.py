"""Standalone outer-loop evolution runner for FeatureBench.

Usage::

    python -m factory.outer_loop.run_evolution \\
        --training-instances 'id1,id2,id3,id4,id5' \\
        --holdout-instances 'id6,id7' \\
        --generations 3 \\
        --population 4 \\
        --budget 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog

from factory.outer_loop.engine import SwarmEngine
from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.harbor_evaluator import HarborEvaluator, create_seed_workflow
from factory.outer_loop.models import SwarmConfig

log = structlog.get_logger()


def main(argv: list[str] | None = None) -> int:
    """Run the evolutionary search loop and print results."""
    parser = argparse.ArgumentParser(
        description="Run outer-loop evolution on FeatureBench via Harbor",
    )
    parser.add_argument(
        "--training-instances",
        required=True,
        help="Comma-separated training instance IDs",
    )
    parser.add_argument(
        "--holdout-instances",
        default="",
        help="Comma-separated holdout instance IDs",
    )
    parser.add_argument(
        "--generations",
        type=int,
        default=3,
        help="Max generations (default: 3)",
    )
    parser.add_argument(
        "--population",
        type=int,
        default=4,
        help="Population size (default: 4)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=30,
        help="Total evaluation budget (default: 30)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-instance solver timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--output",
        default=".factory/outer-loop/best-workflow.json",
        help="Path to write best workflow JSON",
    )

    args = parser.parse_args(argv)

    training = [s.strip() for s in args.training_instances.split(",") if s.strip()]
    holdout = [s.strip() for s in args.holdout_instances.split(",") if s.strip()]

    if not training:
        print(
            "ERROR: --training-instances must contain at least one instance ID",
            file=sys.stderr,
        )
        return 1

    config = SwarmConfig(
        benchmark="featurebench",
        budget=args.budget,
        population_size=args.population,
        training_instances=training,
        holdout_instances=holdout,
    )

    harbor_eval = HarborEvaluator(timeout=args.timeout)
    evaluator = SwarmEvaluator(config, evaluator_fn=harbor_eval)
    engine = SwarmEngine(config=config, evaluator=evaluator)
    seed = create_seed_workflow()

    print("=== Outer Loop Evolution — FeatureBench ===")
    print(f"Seed:       {seed.name} ({len(seed.nodes)} nodes)")
    print(f"Training:   {len(training)} instances")
    print(f"Holdout:    {len(holdout)} instances")
    print(f"Budget:     {args.budget} evaluations")
    print(f"Population: {args.population}")
    print()

    result = engine.run(seed)

    print()
    print("=" * 50)
    print(f"Best score:     {result.best_score:.4f}")
    print(f"Holdout score:  {result.holdout_score:.4f}")
    print(f"Overfit:        {result.overfit_flag}")
    print(f"Convergence:    {result.convergence_reason}")
    print(f"Generations:    {result.generations_completed}")
    print(f"Evaluations:    {result.total_evaluations}")
    print(f"Cost:           ${result.total_cost_usd:.2f}")
    print(f"Archive size:   {result.archive_size}")
    print("=" * 50)

    if result.best_workflow_data:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result.best_workflow_data, indent=2))
        print(f"\nBest workflow written to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
