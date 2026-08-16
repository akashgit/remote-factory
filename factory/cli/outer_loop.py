"""CLI subcommands for the outer loop evolutionary search."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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

    from factory.outer_loop.filesystem import init_filesystem, load_config
    from factory.outer_loop.models import SwarmConfig

    config = load_config(project_path)
    if config is None:
        benchmark = getattr(args, "benchmark", "featurebench")
        budget = getattr(args, "budget", 50)
        population_size = getattr(args, "population_size", 4)
        config = SwarmConfig(
            benchmark=benchmark,
            budget=budget,
            population_size=population_size,
            training_instances=getattr(args, "training_instances", []),
            holdout_instances=getattr(args, "holdout_instances", []),
        )

    root = init_filesystem(project_path, config)
    print(f"Outer loop initialized at {root}")
    print(json.dumps(config.model_dump(mode="json"), indent=2))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Evaluate the current generation."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    generation = getattr(args, "generation", 0)
    print(f"Evaluating generation {generation} at {project_path}")
    return 0


def _cmd_reflect(args: argparse.Namespace) -> int:
    """Run reflection on the current generation."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    generation = getattr(args, "generation", 0)
    print(f"Reflecting on generation {generation} at {project_path}")
    return 0


def _cmd_evolve(args: argparse.Namespace) -> int:
    """Produce the next generation via mutation."""
    project_path = Path(getattr(args, "project_path", ".")).resolve()
    generation = getattr(args, "generation", 0)
    print(f"Evolving generation {generation} at {project_path}")
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

    ev = outer_sub.add_parser("evaluate", help="Evaluate current generation")
    ev.add_argument("project_path", nargs="?", default=".")
    ev.add_argument("--generation", type=int, default=0)

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
