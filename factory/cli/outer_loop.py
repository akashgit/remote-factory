"""CLI handlers for outer-loop calibration and evolution."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import structlog

log = structlog.get_logger()


def cmd_outer_loop(args: argparse.Namespace) -> int:
    """Dispatch outer-loop subcommands."""
    sub = getattr(args, "outer_loop_command", None)
    if sub == "calibrate":
        return cmd_outer_loop_calibrate(args)
    elif sub == "evolve":
        return cmd_outer_loop_evolve(args)
    else:
        print("Usage: factory outer-loop {calibrate,evolve}", file=sys.stderr)
        return 1


def cmd_outer_loop_calibrate(args: argparse.Namespace) -> int:
    """Discover FeatureBench Docker images, run seed workflow, write calibration.json."""
    project = Path(getattr(args, "project", ".")).resolve()
    parallelism = getattr(args, "parallelism", 4)
    timeout = getattr(args, "timeout", 1800)

    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"Error listing Docker images: {result.stderr}", file=sys.stderr)
        return 1

    images = [
        line.strip()
        for line in result.stdout.splitlines()
        if "featurebench" in line.lower()
    ]

    if not images:
        print("No FeatureBench Docker images found. Pull images first.", file=sys.stderr)
        return 1

    log.info("calibration_start", images=len(images), parallelism=parallelism)

    instance_ids = []
    for img in images:
        parts = img.split("/")[-1].split(":")
        instance_ids.append(parts[0])

    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow

    seed_wf = create_seed_workflow()
    evaluator = DirectFeatureBenchEvaluator(
        featurebench_dir=project / "featurebench",
        agent_timeout=timeout,
    )

    results: dict[str, object] = {}
    for iid in instance_ids:
        log.info("calibrating_instance", instance=iid)
        ev = evaluator(seed_wf, str(project), [iid])
        results[iid] = {
            "score": ev.score,
            "resolved": ev.score > 0,
            "details": ev.details,
        }
        log.info("calibration_result", instance=iid, score=ev.score)

    scores = {iid: r["score"] for iid, r in results.items() if isinstance(r, dict)}
    training = [
        iid for iid, s in scores.items()
        if 0.3 <= s <= 0.7
    ]
    holdout = [
        iid for iid in scores
        if iid not in training
    ][:5]

    calibration = {
        "instances": results,
        "training": training,
        "holdout": holdout,
        "total": len(instance_ids),
    }

    out_dir = project / ".factory" / "outer_loop"
    out_dir.mkdir(parents=True, exist_ok=True)
    cal_path = out_dir / "calibration.json"
    tmp_path = cal_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(calibration, indent=2, default=str))
    tmp_path.rename(cal_path)

    log.info(
        "calibration_complete",
        total=len(instance_ids),
        training=len(training),
        holdout=len(holdout),
    )
    print(f"Calibration written to {cal_path}")
    print(f"  Total instances: {len(instance_ids)}")
    print(f"  Training: {len(training)}")
    print(f"  Holdout: {len(holdout)}")
    return 0


def cmd_outer_loop_evolve(args: argparse.Namespace) -> int:
    """Run evolutionary search using calibration data."""
    project = Path(getattr(args, "project", ".")).resolve()
    generations = getattr(args, "generations", 3)
    population = getattr(args, "population", 6)
    parallelism = getattr(args, "parallelism", 4)
    budget = getattr(args, "budget", 40)
    timeout = getattr(args, "timeout", 1800)
    resume = getattr(args, "resume", False)

    cal_path = project / ".factory" / "outer_loop" / "calibration.json"
    if not cal_path.exists():
        print(
            f"No calibration found at {cal_path}. Run 'factory outer-loop calibrate' first.",
            file=sys.stderr,
        )
        return 1

    calibration = json.loads(cal_path.read_text())
    training_instances = calibration.get("training", [])
    holdout_instances = calibration.get("holdout", [])

    if not training_instances:
        print("No training instances in calibration. Re-run calibration.", file=sys.stderr)
        return 1

    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.engine import SwarmEngine
    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow
    from factory.outer_loop.models import SwarmConfig

    config = SwarmConfig(
        benchmark="featurebench",
        budget=budget,
        population_size=population,
        training_instances=training_instances,
        holdout_instances=holdout_instances,
        parallelism=parallelism,
        target_score=getattr(args, "target_score", None),
    )

    direct_eval = DirectFeatureBenchEvaluator(
        featurebench_dir=project / "featurebench",
        agent_timeout=timeout,
    )
    evaluator = SwarmEvaluator(config=config, evaluator_fn=direct_eval)

    engine = SwarmEngine(config=config, evaluator=evaluator)
    seed_wf = create_seed_workflow()

    checkpoint_dir = project / ".factory" / "outer_loop"
    start_gen = 0

    if resume:
        latest = _find_latest_checkpoint(checkpoint_dir)
        if latest is not None:
            log.info("resuming_from_checkpoint", checkpoint=str(latest))
            start_gen = _load_checkpoint_generation(latest)
            print(f"Resuming from generation {start_gen}")

    log.info(
        "evolution_start",
        generations=generations,
        population=population,
        budget=budget,
        training=len(training_instances),
        holdout=len(holdout_instances),
    )

    result = engine.run(seed_wf, str(project))

    results_path = checkpoint_dir / "evolution_results.json"
    tmp_path = results_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    tmp_path.rename(results_path)

    print("\nEvolution complete:")
    print(f"  Best score: {result.best_score:.3f}")
    print(f"  Holdout score: {result.holdout_score:.3f}")
    print(f"  Generations: {result.generations_completed}")
    print(f"  Evaluations: {result.total_evaluations}")
    print(f"  Convergence: {result.convergence_reason}")
    print(f"  Results: {results_path}")
    return 0


def _find_latest_checkpoint(directory: Path) -> Path | None:
    """Find the latest checkpoint file in a directory."""
    checkpoints = sorted(directory.glob("checkpoint_gen_*.json"))
    return checkpoints[-1] if checkpoints else None


def _load_checkpoint_generation(path: Path) -> int:
    """Load the generation number from a checkpoint file."""
    data = json.loads(path.read_text())
    return int(data.get("generation", 0))
