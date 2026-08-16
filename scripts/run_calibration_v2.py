#!/usr/bin/env python3
"""Run calibration with the builder-only seed on the same 10 instances.

Reads instances from calibration.json, evaluates each with the minimal
(builder-only) seed workflow, and writes calibration_v2.json.
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


def main() -> int:
    cal_path = OUTER_LOOP_DIR / "calibration.json"
    if not cal_path.exists():
        print(f"No calibration.json at {cal_path}", file=sys.stderr)
        return 1

    original = json.loads(cal_path.read_text())
    all_instances = list(original.get("instances", {}).keys())
    if not all_instances:
        print("No instances in calibration.json", file=sys.stderr)
        return 1

    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow
    from factory.outer_loop.progress import ProgressTracker

    seed_wf = create_seed_workflow(minimal=True)
    evaluator = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=600,
    )
    progress = ProgressTracker(OUTER_LOOP_DIR)

    print("=" * 60)
    print("Calibration v2 — Builder-Only Seed")
    print("=" * 60)
    print(f"Seed:      {seed_wf.name} ({len(seed_wf.nodes)} node)")
    print(f"Instances: {len(all_instances)}")
    print()

    progress._emit({
        "event_type": "calibration_v2_start",
        "instances": len(all_instances),
        "seed": seed_wf.name,
    })

    results: dict[str, dict[str, object]] = {}
    total_elapsed = 0.0
    resolved_count = 0

    for i, instance_id in enumerate(all_instances, 1):
        print(f"\n[{i}/{len(all_instances)}] {instance_id}")
        progress._emit({
            "event_type": "cal_v2_instance_start",
            "instance": instance_id,
            "index": i,
        })

        start = time.monotonic()
        success = evaluator._eval_instance(seed_wf, instance_id)
        elapsed = round(time.monotonic() - start, 1)
        total_elapsed += elapsed

        score = 1.0 if success else 0.0
        if success:
            resolved_count += 1

        results[instance_id] = {
            "score": score,
            "resolved": success,
            "elapsed_seconds": elapsed,
        }

        status = "PASS" if success else "FAIL"
        print(f"  → {status} ({elapsed:.1f}s)")

        progress._emit({
            "event_type": "cal_v2_instance_done",
            "instance": instance_id,
            "score": score,
            "elapsed": elapsed,
        })

    seed_score = resolved_count / len(all_instances) if all_instances else 0.0

    # Split: 7 training / 3 holdout (same ratio as v1)
    training = all_instances[:7]
    holdout = all_instances[7:]

    cal_v2 = {
        "instances": results,
        "training": training,
        "holdout": holdout,
        "total": len(all_instances),
        "seed_score": round(seed_score, 4),
        "seed_name": seed_wf.name,
        "resolved_count": resolved_count,
        "total_elapsed_seconds": round(total_elapsed, 1),
    }

    out = OUTER_LOOP_DIR / "calibration_v2.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(cal_v2, indent=2))
    tmp.rename(out)

    progress._emit({
        "event_type": "calibration_v2_complete",
        "seed_score": seed_score,
        "resolved": resolved_count,
        "total": len(all_instances),
        "training": len(training),
        "holdout": len(holdout),
    })

    print()
    print("=" * 60)
    print(f"Seed score:  {seed_score:.2%} ({resolved_count}/{len(all_instances)})")
    print(f"Training:    {len(training)} instances")
    print(f"Holdout:     {len(holdout)} instances")
    print(f"Total time:  {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    print(f"Written to:  {out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
