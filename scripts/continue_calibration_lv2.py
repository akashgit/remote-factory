#!/usr/bin/env python3
"""Continue lv2 calibration from instance 4 (instances 1-3 already done, all FAIL)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import structlog

structlog.configure(
    processors=[structlog.dev.ConsoleRenderer(colors=True)],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)
log = structlog.get_logger()

PROJECT_DIR = Path(__file__).resolve().parents[1]
FB_DIR = PROJECT_DIR / "featurebench" / "featurebench"
OUTER_LOOP_DIR = PROJECT_DIR / ".factory" / "outer_loop"

LV2_INSTANCES = [
    "astropy__astropy.b0db0daa.test_basic_rgb.067e927c.lv2",
    "fastapi__fastapi.02e108d1.test_compat.71e8518f.lv2",
    "huggingface__transformers.e2e8dbed.test_modeling_pixtral.a620bb0b.lv2",
    "lightning-ai__pytorch-lightning.126fa6f1.test_fsdp_integration.61c07610.lv2",
    "mlflow__mlflow.93dab383.test_config.c63d41b0.lv2",
    "mwaskom__seaborn.7001ebe7.test_regression.ce8c62e2.lv2",
    "pandas-dev__pandas.82fa2715.test_col.a592871d.lv2",
    "pydata__xarray.97f3a746.test_coordinate_transform.6cacb660.lv2",
    "sympy__sympy.c1097516.test_puiseux.cd575f09.lv2",
    "mesonbuild__meson.f5d81d07.cargotests.8e49c2d0.lv2",
]

ALREADY_DONE = {
    "astropy__astropy.b0db0daa.test_basic_rgb.067e927c.lv2": {
        "score": 0.0, "resolved": False, "elapsed_seconds": 259.2,
    },
    "fastapi__fastapi.02e108d1.test_compat.71e8518f.lv2": {
        "score": 0.0, "resolved": False, "elapsed_seconds": 267.7,
    },
    "huggingface__transformers.e2e8dbed.test_modeling_pixtral.a620bb0b.lv2": {
        "score": 0.0, "resolved": False, "elapsed_seconds": 692.2,
    },
}

RESUME_FROM = 4


def main() -> int:
    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow
    from factory.outer_loop.progress import ProgressTracker

    seed_wf = create_seed_workflow(minimal=True)
    evaluator = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=600,
    )
    progress = ProgressTracker(OUTER_LOOP_DIR)

    missing = [i for i in LV2_INSTANCES[RESUME_FROM - 1:] if not (FB_DIR / i).exists()]
    if missing:
        print(f"Missing instances: {missing}", file=sys.stderr)
        return 1

    print("=" * 60)
    print(f"Calibration lv2 — Resuming from instance {RESUME_FROM}")
    print("=" * 60)
    print(f"Already done: {len(ALREADY_DONE)} (all FAIL)")
    print(f"Remaining:    {len(LV2_INSTANCES) - RESUME_FROM + 1}")
    print()

    results: dict[str, dict[str, object]] = dict(ALREADY_DONE)
    total_elapsed = sum(d["elapsed_seconds"] for d in ALREADY_DONE.values())
    resolved_count = 0

    for i, instance_id in enumerate(LV2_INSTANCES, 1):
        if i < RESUME_FROM:
            continue

        print(f"\n[{i}/{len(LV2_INSTANCES)}] {instance_id}")
        progress._emit({
            "event_type": "cal_lv2_instance_start",
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
            "event_type": "cal_lv2_instance_done",
            "instance": instance_id,
            "score": score,
            "elapsed": elapsed,
        })

    seed_score = resolved_count / len(LV2_INSTANCES)

    # Split: training 7, holdout 3
    # Ensure mix of pass/fail in both splits if possible
    passed = [iid for iid, d in results.items() if d.get("resolved")]
    failed = [iid for iid, d in results.items() if not d.get("resolved")]

    if len(passed) >= 2 and len(failed) >= 2:
        holdout = passed[:1] + failed[:2]
        training = [iid for iid in LV2_INSTANCES if iid not in holdout]
    elif len(passed) >= 1:
        holdout = passed[:1] + failed[:2]
        training = [iid for iid in LV2_INSTANCES if iid not in holdout]
    else:
        training = LV2_INSTANCES[:7]
        holdout = LV2_INSTANCES[7:]

    cal_lv2 = {
        "instances": results,
        "total": len(LV2_INSTANCES),
        "seed_score": round(seed_score, 4),
        "seed_name": seed_wf.name,
        "resolved_count": resolved_count,
        "total_elapsed_seconds": round(total_elapsed, 1),
        "level": "lv2",
        "training": training,
        "holdout": holdout,
    }

    out = OUTER_LOOP_DIR / "calibration_lv2.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(cal_lv2, indent=2))
    tmp.rename(out)

    progress._emit({
        "event_type": "calibration_lv2_complete",
        "seed_score": seed_score,
        "resolved": resolved_count,
        "total": len(LV2_INSTANCES),
        "training_count": len(training),
        "holdout_count": len(holdout),
    })

    print()
    print("=" * 60)
    print(f"Seed score:  {seed_score:.2%} ({resolved_count}/{len(LV2_INSTANCES)})")
    print(f"Total time:  {total_elapsed:.0f}s ({total_elapsed / 60:.1f}min)")
    print(f"Training:    {len(training)} instances")
    print(f"Holdout:     {len(holdout)} instances")
    print(f"Written to:  {out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
