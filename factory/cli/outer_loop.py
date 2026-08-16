"""CLI subcommands for the outer loop evolutionary search."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.outer_loop.evaluator import CycleRecord
    from factory.outer_loop.mode_registry import EphemeralModeRegistry
    from factory.workflow.primitives import Workflow


def _make_inner_loop_factory(
    registry: EphemeralModeRegistry,
) -> Callable[[Workflow], str]:
    """Build a callable that registers a workflow as an ephemeral mode and returns its name.

    This bridges SwarmEvaluator → FeatureBenchInnerLoop: without it,
    _inner_loop_factory is None and evaluation returns a dummy score=0.0.
    """

    def _factory(workflow: Workflow) -> str:
        from factory.outer_loop.similarity import structural_hash

        wf_hash = structural_hash(workflow)
        ind_id = f"eval-{wf_hash[:12]}"
        return registry.register(ind_id, 0, workflow)

    return _factory


def cmd_outer_loop(args: argparse.Namespace) -> int:
    """Dispatch outer-loop subcommands."""
    sub = getattr(args, "outer_loop_command", None)
    if not sub:
        print("Usage: factory outer-loop {calibrate,evaluate,reflect,evolve,status,promote}", file=sys.stderr)
        return 1

    handlers = {
        "calibrate": _cmd_calibrate,
        "evaluate": _cmd_evaluate,
        "reflect": _cmd_reflect,
        "evolve": _cmd_evolve,
        "status": _cmd_status,
        "promote": _cmd_promote,
    }
    handler = handlers.get(sub)
    if handler is None:
        print(f"Unknown outer-loop subcommand: {sub}", file=sys.stderr)
        return 1
    return handler(args)


def _cmd_calibrate(args: argparse.Namespace) -> int:
    """Seed the initial population from a base workflow."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    print(f"Calibrating outer loop for {project_path}")

    from factory.outer_loop.engine import SwarmEngine
    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.filesystem import init_filesystem, load_config, save_checkpoint
    from factory.outer_loop.mode_registry import EphemeralModeRegistry
    from factory.outer_loop.models import OuterLoopState, SwarmConfig

    config = load_config(project_path)
    if config is None:
        benchmark = getattr(args, "benchmark", "featurebench")
        budget = getattr(args, "budget", 50)
        population_size = getattr(args, "population_size", 4)
        designer_count = 0 if benchmark == "featurebench" else 2
        config = SwarmConfig(
            benchmark=benchmark,
            budget=budget,
            population_size=population_size,
            designer_count=designer_count,
            training_instances=getattr(args, "training_instances", []),
            holdout_instances=getattr(args, "holdout_instances", []),
        )

    root = init_filesystem(project_path, config)

    benchmark = config.benchmark
    if benchmark == "featurebench":
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        base_workflow = Workflow(
            name="featurebench-seed",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    model="opus",
                    timeout=7200,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
        )
    else:
        try:
            from factory.workflow.contributed.featurebench.workflow import (
                workflow as featurebench_workflow,
            )

            base_workflow = featurebench_workflow()
        except ImportError:
            print(f"Error: could not load contributed workflow for benchmark '{benchmark}'.", file=sys.stderr)
            return 1

    registry = EphemeralModeRegistry(project_path)
    evaluator = SwarmEvaluator(config, inner_loop_factory=_make_inner_loop_factory(registry))

    from factory.outer_loop.similarity import NoveltyFilter

    min_ged = 1 if len(base_workflow.nodes) <= 2 else 3
    engine = SwarmEngine(
        config=config,
        evaluator=evaluator,
        novelty_filter=NoveltyFilter(min_edit_distance=min_ged),
        mode_registry=registry,
        project_dir=project_path,
    )

    population = engine.seed(base_workflow, config)

    pop_dir = root / "population"
    population.save(pop_dir)

    state = OuterLoopState(
        budget_remaining=config.budget,
        generation=0,
    )
    save_checkpoint(project_path, state)

    modes = registry.list_modes()
    print(f"Outer loop initialized at {root}")
    print(f"Seeded {population.size} individuals ({len(modes)} ephemeral modes):")
    for mode_name in modes:
        print(f"  - {mode_name}")
    print(json.dumps(config.model_dump(mode="json"), indent=2))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Evaluate the current generation's population."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    generation = getattr(args, "generation", 0)
    eval_project_dir = getattr(args, "project_dir", None)
    if eval_project_dir is not None:
        eval_project_dir = str(Path(eval_project_dir).resolve())
    else:
        eval_project_dir = str(project_path)
    print(f"Evaluating generation {generation} at {project_path} (target: {eval_project_dir})")

    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.filesystem import load_checkpoint, load_config, save_checkpoint
    from factory.outer_loop.mode_registry import EphemeralModeRegistry
    from factory.outer_loop.models import OuterLoopState

    config = load_config(project_path)
    if config is None:
        print("Error: no outer loop config found. Run 'factory outer-loop calibrate' first.", file=sys.stderr)
        return 1

    registry = EphemeralModeRegistry(project_path)
    modes = registry.list_modes()
    if not modes:
        print("Error: no ephemeral modes found. Run 'factory outer-loop calibrate' first.", file=sys.stderr)
        return 1

    evaluator = SwarmEvaluator(config, inner_loop_factory=_make_inner_loop_factory(registry))
    results: dict[str, object] = {}
    for mode_name in modes:
        wf = registry.load(mode_name)
        if wf is None:
            continue
        ev = evaluator.evaluate(wf, eval_project_dir, config.training_instances)
        results[mode_name] = {"score": ev.score, "cost_usd": ev.cost_usd}
        print(f"  {mode_name}: score={ev.score:.4f} cost=${ev.cost_usd:.4f}")

    results_dir = project_path / ".factory" / "outer_loop" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / f"gen{generation}.json"
    results_path.write_text(json.dumps(results, indent=2))

    state = load_checkpoint(project_path) or OuterLoopState(budget_remaining=config.budget)
    state = state.model_copy(update={"generation": generation, "total_evaluations": state.total_evaluations + len(results)})
    save_checkpoint(project_path, state)

    print(f"Evaluated {len(results)} candidates. Results saved to {results_path}")
    return 0


def _cmd_reflect(args: argparse.Namespace) -> int:
    """Run contrastive reflection on the current generation."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    generation = getattr(args, "generation", 0)
    print(f"Reflecting on generation {generation} at {project_path}")

    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.filesystem import load_config
    from factory.outer_loop.mode_registry import EphemeralModeRegistry
    from factory.outer_loop.reflector import OuterLoopReflector

    config = load_config(project_path)
    if config is None:
        print("Error: no outer loop config found.", file=sys.stderr)
        return 1

    registry = EphemeralModeRegistry(project_path)
    evaluator = SwarmEvaluator(config, inner_loop_factory=_make_inner_loop_factory(registry))
    reflector = OuterLoopReflector(project_dir=project_path)

    records: list[tuple[str, float, CycleRecord | None]] = []
    for mode_name in registry.list_modes():
        wf = registry.load(mode_name)
        if wf is None:
            continue
        ev = evaluator.evaluate(wf, str(project_path), config.training_instances)
        cycle_rec = evaluator.get_cycle_record(mode_name)
        records.append((mode_name, ev.score, cycle_rec))

    if len(records) < 2:
        print("Not enough candidates for reflection (need >= 2).", file=sys.stderr)
        return 1

    report = reflector.reflect(records, generation)
    print(f"Reflection complete: {len(report.failure_patterns)} failures, "
          f"{len(report.success_patterns)} successes, "
          f"{len(report.mutation_suggestions)} suggestions")
    return 0


def _cmd_evolve(args: argparse.Namespace) -> int:
    """Produce the next generation via mutation and selection."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    generation = getattr(args, "generation", 0)
    print(f"Evolving generation {generation} at {project_path}")

    from factory.outer_loop.filesystem import load_config
    from factory.outer_loop.mode_registry import EphemeralModeRegistry
    from factory.outer_loop.mutations import WeightedRandomStrategy, apply_random_mutation

    config = load_config(project_path)
    if config is None:
        print("Error: no outer loop config found.", file=sys.stderr)
        return 1

    registry = EphemeralModeRegistry(project_path)
    modes = registry.list_modes()
    if not modes:
        print("Error: no ephemeral modes to evolve.", file=sys.stderr)
        return 1

    strategy = WeightedRandomStrategy(mutation_rate=config.mutation_rate)
    offspring_count = 0

    for mode_name in modes[:config.population_size]:
        wf = registry.load(mode_name)
        if wf is None:
            continue
        result = apply_random_mutation(
            wf, strategy, generation + 1,
            frozen_nodes=set(config.frozen_node_ids),
        )
        if result is not None:
            child_wf, mutation_rec = result
            child_id = f"gen{generation + 1}_{offspring_count}"
            registry.register(child_id, generation + 1, child_wf)
            offspring_count += 1
            print(f"  Created offspring {child_id} via {mutation_rec.operator.value}")

    print(f"Evolution complete: {offspring_count} offspring created for generation {generation + 1}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show outer loop progress and metrics."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    check_converge = getattr(args, "check_converge", False)

    from factory.outer_loop.filesystem import load_checkpoint, load_config
    from factory.outer_loop.mode_registry import EphemeralModeRegistry

    config = load_config(project_path)
    state = load_checkpoint(project_path)
    registry = EphemeralModeRegistry(project_path)

    print("=== Outer Loop Status ===")
    if config:
        print(f"Benchmark: {config.benchmark}")
        print(f"Population size: {config.population_size}")
        print(f"Budget: {config.budget}")
    else:
        print("No outer loop config found.")

    if state:
        print(f"Generation: {state.generation}")
        print(f"Total evaluations: {state.total_evaluations}")
        print(f"Best score: {state.best_score:.4f}")
        print(f"Budget remaining: {state.budget_remaining}")
        if state.convergence_reason:
            print(f"Convergence: {state.convergence_reason}")
        if state.score_trajectory:
            print(f"Score trajectory: {[f'{s:.3f}' for s in state.score_trajectory[-5:]]}")
    else:
        print("No checkpoint found — outer loop not started.")

    modes = registry.list_modes()
    print(f"Ephemeral modes: {len(modes)}")

    traj_path = project_path / ".factory" / "outer_loop" / "trajectory.jsonl"
    if traj_path.exists():
        lines = traj_path.read_text().strip().splitlines()
        print(f"Trajectory entries: {len(lines)}")

    events_path = project_path / ".factory" / "outer_loop" / "events.jsonl"
    if events_path.exists():
        lines = events_path.read_text().strip().splitlines()
        print(f"Event log entries: {len(lines)}")

    if check_converge:
        if state and state.convergence_reason:
            print("CONVERGED")
            return 0
        else:
            print("NOT CONVERGED")
            return 1

    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    """Promote the best evolved workflow to a permanent mode."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    mode_name = getattr(args, "mode_name", None)
    permanent_name = getattr(args, "permanent_name", "evolved")

    if not mode_name:
        print("Error: --mode-name required", file=sys.stderr)
        return 1

    from factory.outer_loop.mode_registry import EphemeralModeRegistry

    registry = EphemeralModeRegistry(project_path)
    dest = registry.promote(mode_name, permanent_name)
    if dest:
        print(f"Promoted {mode_name} → {dest}")
        return 0
    else:
        print(f"Failed to promote {mode_name}", file=sys.stderr)
        return 1


def add_outer_loop_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add the outer-loop subcommand group to the CLI parser."""
    outer = subparsers.add_parser(
        "outer-loop",
        help="Evolutionary workflow search",
    )

    outer_sub = outer.add_subparsers(dest="outer_loop_command")

    cal = outer_sub.add_parser("calibrate", help="Seed initial population")
    cal.add_argument("project_path", nargs="?", default=".")
    cal.add_argument("--benchmark", default="featurebench")
    cal.add_argument("--budget", type=int, default=50)
    cal.add_argument("--population-size", type=int, default=4)
    cal.add_argument("--training-instances", nargs="*", default=[])
    cal.add_argument("--holdout-instances", nargs="*", default=[])
    cal.add_argument(
        "--project-dir",
        default=None,
        help="Target project dir for sub-CEO evaluation (defaults to project_path)",
    )

    ev = outer_sub.add_parser("evaluate", help="Evaluate current generation")
    ev.add_argument("project_path", nargs="?", default=".")
    ev.add_argument("--generation", type=int, default=0)
    ev.add_argument(
        "--project-dir",
        default=None,
        help="Target project dir for sub-CEO evaluation (defaults to project_path)",
    )

    ref = outer_sub.add_parser("reflect", help="Run reflection on generation")
    ref.add_argument("project_path", nargs="?", default=".")
    ref.add_argument("--generation", type=int, default=0)

    evo = outer_sub.add_parser("evolve", help="Produce next generation")
    evo.add_argument("project_path", nargs="?", default=".")
    evo.add_argument("--generation", type=int, default=0)

    st = outer_sub.add_parser("status", help="Show progress and metrics")
    st.add_argument("project_path", nargs="?", default=".")
    st.add_argument("--check-converge", action="store_true")

    pr = outer_sub.add_parser("promote", help="Promote best workflow")
    pr.add_argument("project_path", nargs="?", default=".")
    pr.add_argument("--mode-name", required=True)
    pr.add_argument("--permanent-name", default="evolved")
