#!/usr/bin/env python3
"""Continue calibration for remaining 4 instances, then finalize splits."""

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

REMAINING_INSTANCES = [
    "sympy__sympy.c1097516.test_inverse.c240ffe7.lv1",
    "mlflow__mlflow.93dab383.test_abstract_store.e5ff5123.lv1",
    "pytest-dev__pytest.68016f0e.raises_group.c28bf36a.lv1",
    "pypa__packaging.013f3b03.test_metadata.e00b5801.lv1",
]

AGENT_TIMEOUT = 600


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.rename(path)


def emit_progress(event: dict[str, object]) -> None:
    event["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    line = json.dumps(event, default=str)
    with (OUTER_LOOP_DIR / "progress.jsonl").open("a") as f:
        f.write(line + "\n")
    log.info(event.get("event_type", "unknown"), **{k: v for k, v in event.items() if k not in ("event_type", "timestamp")})


def main() -> int:
    from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
    from factory.outer_loop.harbor_evaluator import create_seed_workflow

    cal_path = OUTER_LOOP_DIR / "calibration.json"
    calibration = json.loads(cal_path.read_text())
    existing = calibration.get("instances", {})

    seed_wf = create_seed_workflow()
    evaluator = DirectFeatureBenchEvaluator(
        featurebench_dir=FB_DIR,
        agent_timeout=AGENT_TIMEOUT,
    )

    resolved_count = calibration.get("resolved_so_far", 0)
    total_done = calibration.get("total_so_far", 0)

    for i, iid in enumerate(REMAINING_INSTANCES):
        if iid in existing:
            log.info("skipping_already_done", instance=iid)
            continue

        idx = total_done + i + 1
        emit_progress({"event_type": "cal_instance_start", "instance": iid, "index": idx})

        start = time.monotonic()
        try:
            ev = evaluator(seed_wf, str(PROJECT_DIR), [iid])
            elapsed = time.monotonic() - start
            resolved = ev.score > 0
            existing[iid] = {
                "score": ev.score,
                "resolved": resolved,
                "elapsed_seconds": round(elapsed, 1),
            }
            if resolved:
                resolved_count += 1
        except Exception as exc:
            elapsed = time.monotonic() - start
            existing[iid] = {
                "score": 0.0,
                "resolved": False,
                "elapsed_seconds": round(elapsed, 1),
                "error": str(exc),
            }
            log.error("instance_failed", instance=iid, error=str(exc))

        emit_progress({
            "event_type": "cal_instance_done",
            "instance": iid,
            "score": existing[iid]["score"],
            "elapsed": round(elapsed, 1),
        })

        # Save incrementally
        save_json(cal_path, {
            "instances": existing,
            "resolved_so_far": resolved_count,
            "total_so_far": len(existing),
            "seed_score": resolved_count / len(existing),
        })

    # Finalize: compute training/holdout split
    all_instances = list(existing.keys())
    total = len(all_instances)
    seed_score = resolved_count / max(total, 1)

    # If all resolved, use first 7 for training, last 3 for holdout
    failed = [iid for iid in all_instances if not existing[iid].get("resolved", False)]
    passed = [iid for iid in all_instances if existing[iid].get("resolved", False)]

    if len(failed) >= 3:
        training = failed
        holdout = passed[:3] if len(passed) >= 3 else passed
    else:
        training = all_instances[:7]
        holdout = all_instances[7:]

    total_elapsed = sum(
        existing[iid].get("elapsed_seconds", 0)
        for iid in all_instances
        if isinstance(existing[iid], dict)
    )

    final_cal = {
        "instances": existing,
        "training": training,
        "holdout": holdout,
        "total": total,
        "seed_score": seed_score,
        "resolved_count": resolved_count,
        "total_elapsed_seconds": round(total_elapsed, 1),
    }

    save_json(cal_path, final_cal)

    emit_progress({
        "event_type": "calibration_complete",
        "seed_score": seed_score,
        "resolved": resolved_count,
        "total": total,
        "training": len(training),
        "holdout": len(holdout),
    })

    log.info(
        "calibration_finalized",
        seed_score=f"{seed_score:.2f}",
        resolved=resolved_count,
        total=total,
        training=len(training),
        holdout=len(holdout),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
