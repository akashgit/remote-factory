#!/usr/bin/env python3
"""Generate stratified train/val/test splits from the FeatureBench dataset.

Loads 200 tasks from HuggingFace LiberCoders/FeatureBench, selects 40 via
stratified sampling by repo, then splits 20/10/10 into train/val/test.
Deterministic with seed=42.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from datasets import load_dataset
from sklearn.model_selection import train_test_split

SEED = 42
TOTAL_SAMPLE = 40
TRAIN_SIZE = 20
VAL_SIZE = 10
TEST_SIZE = 10
MIN_GROUP_SIZE = 2

OUTPUT_DIR = Path(__file__).parent


def _parse_level(instance_id: str) -> int:
    if instance_id.endswith(".lv2"):
        return 2
    return 1


def _stratify_labels(tasks: list[dict], key: str) -> list[str]:
    """Build stratification labels, merging groups with <MIN_GROUP_SIZE members."""
    counts = Counter(t[key] for t in tasks)
    return [t[key] if counts[t[key]] >= MIN_GROUP_SIZE else "_other" for t in tasks]


def main() -> None:
    ds = load_dataset("LiberCoders/FeatureBench", split="full")

    tasks = []
    for row in ds:
        iid = row["instance_id"]
        repo = iid.rsplit("/", 1)[0] if "/" in iid else iid.split("__")[0]
        tasks.append({
            "instance_id": iid,
            "repo": repo,
            "level": _parse_level(iid),
        })

    strat_labels = _stratify_labels(tasks, "repo")

    sampled, _ = train_test_split(
        tasks,
        train_size=TOTAL_SAMPLE,
        stratify=strat_labels,
        random_state=SEED,
    )

    sampled_strat = _stratify_labels(sampled, "repo")

    train_val, test = train_test_split(
        sampled,
        test_size=TEST_SIZE,
        stratify=sampled_strat,
        random_state=SEED,
    )

    train_val_strat = _stratify_labels(train_val, "repo")

    train, val = train_test_split(
        train_val,
        test_size=VAL_SIZE,
        stratify=train_val_strat,
        random_state=SEED,
    )

    for name, split in [("train", train), ("val", val), ("test", test)]:
        path = OUTPUT_DIR / f"{name}.jsonl"
        with path.open("w") as f:
            for task in sorted(split, key=lambda t: t["instance_id"]):
                f.write(json.dumps(task) + "\n")
        print(f"Wrote {len(split)} tasks to {path}")

    print(f"\nTotal: {len(train)} train + {len(val)} val + {len(test)} test = {len(sampled)}")


if __name__ == "__main__":
    main()
