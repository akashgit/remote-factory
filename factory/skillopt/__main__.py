"""CLI entry point for SkillOpt: python -m factory.skillopt."""
from __future__ import annotations

import argparse
import sys


_ADAPTERS = {
    "swebench": "factory.skillopt.adapters.swebench:SwebenchAdapter",
    "featurebench": "factory.skillopt.adapters.featurebench:FeaturebenchAdapter",
    "programbench": "factory.skillopt.adapters.programbench:ProgrambenchAdapter",
    "terminalbench": "factory.skillopt.adapters.terminalbench:TerminalbenchAdapter",
    "legacybench": "factory.skillopt.adapters.legacybench:LegacybenchAdapter",
}


def _load_adapter(name: str):
    if name not in _ADAPTERS:
        print(f"Unknown adapter: {name}. Available: {', '.join(_ADAPTERS)}", file=sys.stderr)
        sys.exit(1)
    module_path, class_name = _ADAPTERS[name].rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SkillOpt — benchmark-driven SKILL.md optimization loop",
    )
    parser.add_argument(
        "--benchmark",
        required=True,
        choices=list(_ADAPTERS),
        help="Benchmark adapter to use",
    )
    parser.add_argument(
        "--skill-path",
        required=True,
        help="Path to SKILL.md to optimize",
    )
    parser.add_argument(
        "--adapter",
        default=None,
        help="Override adapter name (default: same as --benchmark)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=3,
        help="Number of training epochs (default: 3)",
    )
    parser.add_argument(
        "--steps-per-epoch",
        type=int,
        default=5,
        help="Steps per epoch (default: 5)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Rollout batch size (default: 8)",
    )
    parser.add_argument(
        "--learning-rate",
        type=int,
        default=3,
        help="Max edits per step (default: 3)",
    )
    parser.add_argument(
        "--eval-split-seed",
        type=int,
        default=42,
        help="Seed for train/eval split (default: 42)",
    )
    parser.add_argument(
        "--metric",
        choices=["hard", "soft", "mixed"],
        default="hard",
        help="Gate metric (default: hard)",
    )
    parser.add_argument(
        "--out-dir",
        default=".skillopt",
        help="Output directory for checkpoints and logs (default: .skillopt)",
    )
    parser.add_argument(
        "--results-dir",
        default="",
        help="Path to benchmark results directory",
    )
    parser.add_argument(
        "--instances-file",
        default="",
        help="Path to JSON file with benchmark instance IDs",
    )
    parser.add_argument(
        "--instances",
        default="",
        help="Comma-separated list of instance IDs to pin (runs the same tasks every rollout)",
    )
    parser.add_argument(
        "--overfit",
        action="store_true",
        help="Overfit mode: eval on same tasks as training (no separate eval split)",
    )
    parser.add_argument(
        "--results-from",
        default="",
        help="Path to existing rollout results JSON to use as first-step baseline",
    )
    args = parser.parse_args()

    adapter_name = args.adapter or args.benchmark
    adapter = _load_adapter(adapter_name)
    instances = [s.strip() for s in args.instances.split(",") if s.strip()] if args.instances else []
    adapter.setup({
        "results_dir": args.results_dir,
        "instances_file": args.instances_file,
        "skill_path": args.skill_path,
        "instances": instances,
    })

    from factory.skillopt.trainer import SkillOptTrainer

    trainer = SkillOptTrainer(
        adapter=adapter,
        skill_path=args.skill_path,
        epochs=args.epochs,
        steps_per_epoch=args.steps_per_epoch,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        eval_split_seed=args.eval_split_seed,
        metric=args.metric,
        out_dir=args.out_dir,
        overfit=args.overfit,
        results_from=args.results_from,
    )
    trainer.train()
    return 0


if __name__ == "__main__":
    sys.exit(main())
