"""CLI entry point for SkillOpt: python -m factory.skillopt."""
from __future__ import annotations

import argparse
import json
import sys

from factory.skillopt.loop import run_cycle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SkillOpt — benchmark-driven prompt optimization loop (PoC)",
    )
    parser.add_argument(
        "--benchmark",
        required=True,
        help="Benchmark name (e.g. swebench, featurebench)",
    )
    parser.add_argument(
        "--workflow",
        required=True,
        help="Path to workflow .py file",
    )
    parser.add_argument(
        "--node-id",
        default="builder",
        help="AgentNode id whose prompt_template to optimize (default: builder)",
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Path to directory of benchmark result JSON files",
    )
    parser.add_argument(
        "--learning-rate",
        type=int,
        default=3,
        help="Max edits per cycle (default: 3)",
    )
    parser.add_argument(
        "--cycle-id",
        type=int,
        default=1,
        help="Cycle number for tracking (default: 1)",
    )
    parser.add_argument(
        "--skip-rollout",
        action="store_true",
        help="Skip benchmark re-run (dry run mode)",
    )
    args = parser.parse_args()

    result = run_cycle(
        benchmark=args.benchmark,
        workflow_file=args.workflow,
        node_id=args.node_id,
        results_dir=args.results_dir,
        learning_rate=args.learning_rate,
        cycle_id=args.cycle_id,
        skip_rollout=args.skip_rollout,
    )

    print(json.dumps(result.model_dump(), indent=2))
    return 0 if result.accepted or result.score_after is None else 1


if __name__ == "__main__":
    sys.exit(main())
