#!/usr/bin/env python3
"""Load SearchQA data and export in Harbor-compatible JSONL format.

Usage:
    python -m benchmarks.searchqa.dataset --out-dir benchmarks/searchqa/data [--limit 50]

Loads from ``lucadiliello/searchqa`` on HuggingFace.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


_HF_SPLIT_MAP = {
    "train": "train",
    "val": "validation",
}


def load_from_huggingface(split: str = "train", limit: int | None = None) -> list[dict]:
    """Load SearchQA from HuggingFace ``lucadiliello/searchqa``."""
    from datasets import load_dataset as hf_load_dataset  # type: ignore[import-untyped]

    hf_split = _HF_SPLIT_MAP.get(split, split)
    ds = hf_load_dataset("lucadiliello/searchqa", split=hf_split)

    items: list[dict] = []
    for i, row in enumerate(ds):
        if limit and i >= limit:
            break
        items.append({
            "id": row["key"],
            "question": row["question"],
            "context": row["context"],
            "answers": row["answers"],
        })
    return items


def load_dataset_items(split: str = "train", limit: int | None = None) -> list[dict]:
    """Load dataset from HuggingFace."""
    return load_from_huggingface(split=split, limit=limit)


def export_jsonl(items: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SearchQA dataset")
    parser.add_argument("--out-dir", default="benchmarks/searchqa/data")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    total = 0
    for split in ("train", "val"):
        items = load_dataset_items(split=split, limit=args.limit)
        export_jsonl(items, out_dir / f"{split}.jsonl")
        total += len(items)
        print(f"Exported {len(items)} items to {out_dir / f'{split}.jsonl'}")
    print(f"Total: {total} items across 2 splits")


if __name__ == "__main__":
    main()
