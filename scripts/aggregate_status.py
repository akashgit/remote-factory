#!/usr/bin/env python3
"""Aggregate per-stage results into build-root-status.json."""
from __future__ import annotations

import json
import os
import sys

STAGES = [
    {"num": 1, "name": "DEP_RESOLVE", "file": "stage1-result.json"},
    {"num": 2, "name": "ARTIFACT_RECOVERY", "file": "stage2-result.json"},
    {"num": 3, "name": "COMPILE", "file": "stage3-result.json"},
    {"num": 4, "name": "TEST", "file": "stage4-result.json"},
]


def is_stage_complete(stage_num: int, data: dict) -> bool:
    if stage_num == 1:
        return data.get("failed", 1) == 0
    if stage_num == 2:
        return True
    if stage_num == 3:
        return data.get("failed", 1) == 0
    if stage_num == 4:
        return data.get("failed", 1) == 0 and data.get("errors", 1) == 0
    return False


def aggregate(results_dir: str) -> dict:
    stage_completed = 0
    current_stage = None
    stages: list[dict] = []

    for stage in STAGES:
        path = os.path.join(results_dir, stage["file"])
        if not os.path.exists(path):
            stages.append({
                "name": stage["name"],
                "status": "pending",
                "terminal_condition_met": False,
            })
            if current_stage is None:
                current_stage = {"name": stage["name"], "num": stage["num"]}
            continue

        with open(path) as f:
            data = json.load(f)

        complete = is_stage_complete(stage["num"], data)
        stages.append({
            "name": stage["name"],
            "status": "complete" if complete else "in_progress",
            "terminal_condition_met": complete,
            "result": data,
        })

        if complete:
            stage_completed = stage["num"]
        elif current_stage is None:
            current_stage = {"name": stage["name"], "num": stage["num"]}

    return {
        "stage_completed": stage_completed,
        "current_stage": current_stage,
        "stages": stages,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: aggregate_status.py <results_dir>", file=sys.stderr)
        sys.exit(1)
    result = aggregate(sys.argv[1])
    json.dump(result, sys.stdout, indent=2)
    print()
