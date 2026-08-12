"""CLI handler for the optimize subcommand — runs the inner-outer optimization loop."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


def _run_legacy(args: argparse.Namespace, project: Path) -> int:
    """Legacy path: use OptimizationLoop directly."""
    from factory.optimization import AgenticMutator, LoopConfig, OptimizationLoop, Surface
    from factory.optimization.benchmarks.harbor import HarborBenchmark
    from factory.optimization.protocols import Evaluator, Executor
    from factory.optimization.types import BenchmarkSplits

    benchmark = getattr(args, "benchmark", None) or "searchqa"
    benchmark_dir_str = getattr(args, "benchmark_dir", None)
    git_ref = getattr(args, "git_ref", None) or os.environ.get("FACTORY_GIT_REF", "main")
    docker_host = getattr(args, "docker_host", None) or os.environ.get("DOCKER_HOST", "")
    concurrency = getattr(args, "concurrency", 5)
    steps = getattr(args, "steps", 3)
    epochs = getattr(args, "epochs", 1)
    split_seed = getattr(args, "split_seed", 42)
    splits_dir_str = getattr(args, "splits_dir", None)

    evaluator: Evaluator
    executor: Executor
    model: str

    use_dynamic = benchmark_dir_str is not None or benchmark == "auto"

    if use_dynamic:
        from factory.optimization.benchmarks.loader import load_benchmark

        if benchmark_dir_str:
            benchmark_dir = Path(benchmark_dir_str).resolve()
        else:
            benchmark_dir = project / ".factory" / "eval" / "benchmark"

        if not benchmark_dir.is_dir():
            print(f"Error: benchmark directory does not exist: {benchmark_dir}", file=sys.stderr)
            return 1

        try:
            defn = load_benchmark(benchmark_dir)
        except ValueError as exc:
            print(f"Error: failed to load benchmark: {exc}", file=sys.stderr)
            return 1

        model = getattr(args, "model", None) or "sonnet"
        surface = Surface()
        executor_params = defn.config.get("executor_params", {})
        evaluator_params = defn.config.get("evaluator_params", {})
        executor = defn.executor_cls(**executor_params)
        evaluator = defn.evaluator_cls(**evaluator_params)
        benchmark = defn.name
        print(f"Loaded dynamic benchmark: {defn.name} (from {benchmark_dir})")

    else:
        match benchmark:
            case "searchqa":
                from factory.optimization.benchmarks.searchqa import SearchQAEvaluator, create_searchqa_splits

                model = getattr(args, "model", None) or "sonnet"
                default_skill = (
                    "# Question Answering Skill\n\n"
                    "(No learned rules yet.)\n\n"
                    "## Instructions\n\n"
                    "Read the question and search results from /tmp/task-instruction.md.\n"
                    "Answer the question and write ONLY your final answer to /workspace/answer.txt.\n"
                    "Also include your answer in <answer> tags in your response.\n"
                )
                surface = Surface(prompt_slots={"skill": default_skill})
                skill_path = getattr(args, "skill_path", None)
                if skill_path:
                    sp = Path(skill_path)
                    if sp.exists():
                        surface.prompt_slots["skill"] = sp.read_text()

                splits: BenchmarkSplits | None = None
                if splits_dir_str:
                    splits = BenchmarkSplits.from_jsonl_dir(Path(splits_dir_str).resolve())
                else:
                    auto_dir = project / ".factory" / "eval" / "benchmark" / "splits"
                    if auto_dir.is_dir():
                        splits = BenchmarkSplits.from_jsonl_dir(auto_dir)
                    else:
                        tasks_dir = project / "benchmarks" / "searchqa-harbor" / "train"
                        if tasks_dir.is_dir():
                            splits = create_searchqa_splits(tasks_dir, seed=split_seed)

                if splits:
                    warnings = splits.validate()
                    for w in warnings:
                        print(f"  Split warning: {w}", file=sys.stderr)
                    print(f"Splits: train={len(splits.train_ids)} dev={len(splits.dev_ids)} "
                          f"eval={len(splits.eval_ids)} test={len(splits.test_ids)}")

                executor = HarborBenchmark(
                    git_ref=git_ref,
                    concurrency=concurrency,
                    docker_host=docker_host,
                    model=model,
                    splits=splits,
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
    if result.dev_score is not None:
        print(f"Dev score:   {result.dev_score:.4f}")
    if result.test_score is not None:
        print(f"Test score:  {result.test_score:.4f}")
    if result.steps:
        total_delta = result.final_score - (result.steps[0].score_start or 0.0)
        print(f"Total improvement: {total_delta:+.4f}")

    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    """Run the optimization loop with HarborBenchmark executor."""
    project = Path(args.path).resolve()
    if not project.exists():
        print(f"Error: project path does not exist: {project}", file=sys.stderr)
        return 1

    legacy = getattr(args, "legacy", False)
    if legacy:
        return _run_legacy(args, project)

    benchmark = getattr(args, "benchmark", None) or "searchqa"
    git_ref = getattr(args, "git_ref", None) or os.environ.get("FACTORY_GIT_REF", "main")
    docker_host = getattr(args, "docker_host", None) or os.environ.get("DOCKER_HOST", "")
    concurrency = getattr(args, "concurrency", 5)
    steps = getattr(args, "steps", 3)
    model = getattr(args, "model", None) or "sonnet"
    skill_path = getattr(args, "skill_path", None)

    # Setup: write initial state files
    opt_dir = project / ".factory" / "optimization"
    opt_dir.mkdir(parents=True, exist_ok=True)

    # Initial skill
    skill_file = opt_dir / "current_skill.md"
    if skill_path:
        sp = Path(skill_path)
        if sp.exists():
            skill_file.write_text(sp.read_text())
    elif not skill_file.exists():
        default_skill = (
            "# Question Answering Skill\n\n"
            "(No learned rules yet.)\n\n"
            "## Instructions\n\n"
            "Read the question and search results from /tmp/task-instruction.md.\n"
            "Answer the question and write ONLY your final answer to /workspace/answer.txt.\n"
            "Also include your answer in <answer> tags in your response.\n"
        )
        skill_file.write_text(default_skill)

    # Initial state.json
    state_file = opt_dir / "state.json"
    initial_state = {
        "step": 0,
        "current_score": 0.0,
        "best_score": 0.0,
        "best_step": 0,
        "history": [],
    }
    state_file.write_text(json.dumps(initial_state, indent=2) + "\n")

    # Pass config to optimize-step via env vars
    env = os.environ.copy()
    env["FACTORY_OPT_BENCHMARK"] = benchmark
    env["FACTORY_OPT_CONCURRENCY"] = str(concurrency)
    env["FACTORY_GIT_REF"] = git_ref
    env["FACTORY_OPT_MODEL"] = model
    env["FACTORY_OPT_MAX_ITERATIONS"] = str(steps)
    if docker_host:
        env["DOCKER_HOST"] = docker_host

    print(f"Starting optimization (workflow): benchmark={benchmark}, max_iterations={steps}, concurrency={concurrency}")

    # Execute workflow
    cmd = ["factory", "workflow", "run", "optimize", str(project)]
    result = subprocess.run(cmd, env=env)

    # Read results
    test_result_path = opt_dir / "test_result.json"
    state: dict[str, Any] = json.loads(state_file.read_text()) if state_file.exists() else dict(initial_state)

    print(f"\n{'='*50}")
    history: list[dict[str, Any]] = state.get("history", [])
    print(f"Training complete: {len(history)} steps")

    if history:
        baseline_score: float = history[0].get("score_start", 0.0)
        print(f"Baseline score: {baseline_score:.4f}")
        for h in history:
            delta: float = h.get("score_delta", 0.0)
            print(f"  Step {h['step']}: {h['score_start']:.4f} -> {h['score_end']:.4f} "
                  f"({delta:+.4f}) verdict={h.get('verdict', 'n/a')}")

    print(f"Best score: {state['best_score']:.4f} (step {state['best_step']})")
    print(f"Final score: {state['current_score']:.4f}")

    if test_result_path.exists():
        test_data = json.loads(test_result_path.read_text())
        print(f"Test score:  {test_data['score']:.4f}")

    if history:
        total_delta_f: float = state["current_score"] - history[0].get("score_end", 0.0)
        print(f"Total improvement: {total_delta_f:+.4f}")

    return result.returncode
