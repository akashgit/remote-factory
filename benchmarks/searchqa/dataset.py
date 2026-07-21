#!/usr/bin/env python3
"""Load SearchQA data and export in Harbor-compatible JSONL format.

Usage:
    python -m benchmarks.searchqa.dataset --out-dir benchmarks/searchqa/data [--limit 50]

If HuggingFace ``datasets`` is installed, loads from ``kyunghyuncho/search_qa``.
Otherwise, falls back to a small built-in sample for testing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


SAMPLE_DATA: list[dict] = [
    {
        "id": "searchqa-001",
        "question": "What is the capital of France?",
        "context": "France is a country in Western Europe. Its capital city is Paris, which is known for the Eiffel Tower.",
        "answers": ["Paris"],
    },
    {
        "id": "searchqa-002",
        "question": "Who wrote Romeo and Juliet?",
        "context": "Romeo and Juliet is a tragedy written by William Shakespeare early in his career.",
        "answers": ["William Shakespeare"],
    },
    {
        "id": "searchqa-003",
        "question": "What is the chemical symbol for water?",
        "context": "Water is a chemical substance with the chemical formula H2O. A water molecule contains one oxygen and two hydrogen atoms.",
        "answers": ["H2O"],
    },
    {
        "id": "searchqa-004",
        "question": "In what year did World War II end?",
        "context": "World War II ended in 1945 with the surrender of Germany in May and Japan in September.",
        "answers": ["1945"],
    },
    {
        "id": "searchqa-005",
        "question": "What planet is known as the Red Planet?",
        "context": "Mars is the fourth planet from the Sun. It is often called the Red Planet because of its reddish appearance.",
        "answers": ["Mars"],
    },
    {
        "id": "searchqa-006",
        "question": "Who painted the Mona Lisa?",
        "context": "The Mona Lisa is a half-length portrait painting by Italian artist Leonardo da Vinci. It has been described as the best known painting in the world.",
        "answers": ["Leonardo da Vinci"],
    },
    {
        "id": "searchqa-007",
        "question": "What is the largest ocean on Earth?",
        "context": "The Pacific Ocean is the largest and deepest of Earth's five oceanic divisions. It covers an area of about 165.25 million square kilometers.",
        "answers": ["Pacific Ocean", "the Pacific Ocean"],
    },
    {
        "id": "searchqa-008",
        "question": "What element has the atomic number 1?",
        "context": "Hydrogen is the lightest element and has atomic number 1. It is the most abundant chemical substance in the universe.",
        "answers": ["Hydrogen"],
    },
    {
        "id": "searchqa-009",
        "question": "Who was the first President of the United States?",
        "context": "George Washington served as the first President of the United States from 1789 to 1797.",
        "answers": ["George Washington"],
    },
    {
        "id": "searchqa-010",
        "question": "What is the speed of light in a vacuum?",
        "context": "The speed of light in a vacuum is approximately 299,792,458 meters per second, or about 186,000 miles per second.",
        "answers": ["299,792,458 meters per second", "approximately 299,792,458 meters per second"],
    },
    {
        "id": "searchqa-011",
        "question": "What is the tallest mountain in the world?",
        "context": "Mount Everest, located in the Himalayas on the border of Nepal and Tibet, is the tallest mountain in the world at 8,849 meters above sea level.",
        "answers": ["Mount Everest"],
    },
    {
        "id": "searchqa-012",
        "question": "What language has the most native speakers?",
        "context": "Mandarin Chinese has the most native speakers of any language in the world, with over 900 million native speakers.",
        "answers": ["Mandarin Chinese", "Mandarin", "Chinese"],
    },
    {
        "id": "searchqa-013",
        "question": "What is the smallest country in the world?",
        "context": "Vatican City is the smallest country in the world by both area and population. It is an independent city-state enclaved within Rome, Italy.",
        "answers": ["Vatican City"],
    },
    {
        "id": "searchqa-014",
        "question": "Who developed the theory of relativity?",
        "context": "Albert Einstein developed the theory of relativity, one of the two pillars of modern physics. He published the special theory of relativity in 1905.",
        "answers": ["Albert Einstein"],
    },
    {
        "id": "searchqa-015",
        "question": "What is the hardest natural substance?",
        "context": "Diamond is the hardest known natural material on the Mohs scale of mineral hardness. It scores a 10 on the scale.",
        "answers": ["Diamond", "diamond"],
    },
    {
        "id": "searchqa-016",
        "question": "What gas do plants absorb from the atmosphere?",
        "context": "Plants absorb carbon dioxide from the atmosphere during photosynthesis and release oxygen as a byproduct.",
        "answers": ["carbon dioxide", "CO2"],
    },
    {
        "id": "searchqa-017",
        "question": "What is the longest river in the world?",
        "context": "The Nile River, flowing through northeastern Africa, is generally considered the longest river in the world at approximately 6,650 kilometers.",
        "answers": ["the Nile River", "Nile", "the Nile"],
    },
    {
        "id": "searchqa-018",
        "question": "Who wrote 'A Brief History of Time'?",
        "context": "A Brief History of Time is a popular science book on cosmology by English physicist Stephen Hawking, first published in 1988.",
        "answers": ["Stephen Hawking"],
    },
    {
        "id": "searchqa-019",
        "question": "What is the most abundant gas in Earth's atmosphere?",
        "context": "Nitrogen is the most abundant gas in Earth's atmosphere, making up about 78% of the atmosphere by volume.",
        "answers": ["Nitrogen"],
    },
    {
        "id": "searchqa-020",
        "question": "In what year was the Declaration of Independence signed?",
        "context": "The United States Declaration of Independence was adopted by the Second Continental Congress on July 4, 1776.",
        "answers": ["1776"],
    },
]

_HF_SPLIT_MAP = {
    "train": "train",
    "val": "validation",
    "test": "test",
}


def load_from_huggingface(split: str = "train", limit: int | None = None) -> list[dict]:
    """Load SearchQA from HuggingFace ``kyunghyuncho/search_qa`` (raw_jeopardy config)."""
    try:
        from datasets import load_dataset  # type: ignore[import-untyped]
    except ImportError:
        return []

    hf_split = _HF_SPLIT_MAP.get(split, split)
    try:
        ds = load_dataset("kyunghyuncho/search_qa", "raw_jeopardy", split=hf_split, trust_remote_code=True)
    except Exception:
        return []

    items: list[dict] = []
    for i, row in enumerate(ds):
        if limit and i >= limit:
            break
        snippets = row.get("search_results", [])
        if isinstance(snippets, list):
            context = " [DOC] ".join(s for s in snippets if isinstance(s, str))
        else:
            context = str(snippets)
        answer = row.get("answer", "")
        items.append({
            "id": f"searchqa-{split}-{i:05d}",
            "question": row.get("question", ""),
            "context": context,
            "answers": [answer] if isinstance(answer, str) else answer,
        })
    return items


def load_dataset_items(split: str = "train", limit: int | None = None) -> list[dict]:
    """Load dataset — tries HuggingFace first, falls back to built-in sample."""
    items = load_from_huggingface(split=split, limit=limit)
    if items:
        return items
    data = SAMPLE_DATA
    if limit:
        data = data[:limit]
    return data


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
    for split in ("train", "val", "test"):
        items = load_dataset_items(split=split, limit=args.limit)
        export_jsonl(items, out_dir / f"{split}.jsonl")
        total += len(items)
        print(f"Exported {len(items)} items to {out_dir / f'{split}.jsonl'}")
    print(f"Total: {total} items across 3 splits")


if __name__ == "__main__":
    main()
