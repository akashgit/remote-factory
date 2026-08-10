"""CLI handler for the optimize subcommand — runs the inner-outer optimization loop."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def cmd_optimize(args: argparse.Namespace) -> int:
    """Run the optimization loop with HarborBenchmark executor."""
    project = Path(args.path).resolve()
    if not project.exists():
        print(f"Error: project path does not exist: {project}", file=sys.stderr)
        return 1

    from factory.optimization import AgenticMutator, LoopConfig, OptimizationLoop, Surface
    from factory.optimization.benchmarks.harbor import HarborBenchmark
    from factory.optimization.protocols import Evaluator, Executor

    benchmark = getattr(args, "benchmark", None) or "searchqa"
    git_ref = getattr(args, "git_ref", None) or "main"
    docker_host = getattr(args, "docker_host", None) or os.environ.get("DOCKER_HOST", "")
    concurrency = getattr(args, "concurrency", 5)
    steps = getattr(args, "steps", 3)
    epochs = getattr(args, "epochs", 1)

    evaluator: Evaluator
    executor: Executor
    model: str

    match benchmark:
        case "searchqa":
            from factory.optimization.benchmarks.searchqa import SearchQAEvaluator

            model = getattr(args, "model", None) or "sonnet"
            surface = Surface()
            skill_path = getattr(args, "skill_path", None)
            if skill_path:
                sp = Path(skill_path)
                if sp.exists():
                    surface.prompt_slots["skill"] = sp.read_text()
            executor = HarborBenchmark(
                git_ref=git_ref,
                concurrency=concurrency,
                docker_host=docker_host,
                model=model,
            )
            evaluator = SearchQAEvaluator()

        case "featurebench":
            from factory.optimization.benchmarks.featurebench import (
                FeatureBenchEvaluator,
                build_featurebench_executor,
                build_featurebench_surface,
            )

            model = getattr(args, "model", None) or "opus"
            surface = build_featurebench_surface()
            executor = build_featurebench_executor(
                git_ref=git_ref,
                concurrency=concurrency,
                docker_host=docker_host,
                model=model,
            )
            evaluator = FeatureBenchEvaluator()

        case _:
            print(f"Error: unsupported benchmark: {benchmark}", file=sys.stderr)
            return 1

    mutator = AgenticMutator(project_path=project, model=model)

    config = LoopConfig(
        epochs=epochs,
        steps_per_epoch=steps,
    )

    loop = OptimizationLoop(
        project_dir=project,
        surface=surface,
        executor=executor,
        evaluator=evaluator,
        mutator=mutator,
        config=config,
    )

    print(f"Starting optimization: benchmark={benchmark}, steps={steps}, epochs={epochs}, concurrency={concurrency}")
    result = loop.train()

    print(f"\n{'='*50}")
    print(f"Training complete: {len(result.steps)} steps")
    if result.steps:
        print(f"Baseline score: {result.steps[0].score_start:.4f}")
        for s in result.steps:
            delta = f"{s.score_delta:+.4f}" if s.score_delta is not None else "n/a"
            print(f"  Step {s.step_number}: {s.score_start:.4f} -> {s.score_end:.4f} ({delta}) verdict={s.verdict}")
    print(f"Best score: {result.best_score:.4f} (step {result.best_step})")
    print(f"Final score: {result.final_score:.4f}")
    if result.steps:
        total_delta = result.final_score - (result.steps[0].score_start or 0.0)
        print(f"Total improvement: {total_delta:+.4f}")

    return 0
