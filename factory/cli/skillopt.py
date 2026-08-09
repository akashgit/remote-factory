"""CLI handler for the skillopt subcommand — unified optimization loop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_skillopt(args: argparse.Namespace) -> int:
    """Run the unified optimization loop."""
    project = Path(args.path).resolve()
    if not project.exists():
        print(f"Error: project path does not exist: {project}", file=sys.stderr)
        return 1

    from factory.optimization import OptimizationLoop, Surface
    from factory.optimization.executors import FactoryCeoExecutor
    from factory.optimization.mutators import UnifiedMutator
    from factory.optimization.types import LoopConfig

    config = LoopConfig(
        epochs=args.epochs,
        steps_per_epoch=args.steps_per_epoch,
    )

    surface = Surface()
    if args.skill_path:
        skill = Path(args.skill_path)
        if skill.exists():
            surface.prompt_slots["skill"] = skill.read_text()

    if args.benchmark == "searchqa":
        from factory.optimization.benchmarks.searchqa import (
            SearchQAEvaluator,
            build_searchqa_executor,
        )
        executor = build_searchqa_executor()
        evaluator = SearchQAEvaluator()
    else:
        from factory.inner_loop import CirclePackingEvaluator
        executor = FactoryCeoExecutor()
        evaluator = CirclePackingEvaluator()

    mutator = UnifiedMutator()

    loop = OptimizationLoop(
        project_dir=project,
        surface=surface,
        executor=executor,
        evaluator=evaluator,
        mutator=mutator,
        config=config,
    )

    result = loop.train()
    print(f"Training complete: {len(result.steps)} steps")
    print(f"Best score: {result.best_score:.4f} (step {result.best_step})")
    print(f"Final score: {result.final_score:.4f}")
    return 0
