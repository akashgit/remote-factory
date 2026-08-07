#!/usr/bin/env python3
"""Post-evaluation script for SearchQA benchmark.

Reads the agent's output from Harbor's jobs directory, extracts the answer
from <answer>...</answer> tags, and scores against gold answers using EM/F1.

Usage:
    python benchmarks/searchqa/eval.py <jobs_dir> --dataset benchmarks/searchqa/data/searchqa.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from searchqa.evaluator import score_prediction  # noqa: E402

_ANSWER_TAG_PATTERN = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)


def extract_answer(text: str) -> str:
    """Extract the answer from <answer>...</answer> tags in agent output."""
    match = _ANSWER_TAG_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def load_gold_answers(dataset_path: Path) -> dict[str, list[str]]:
    """Load gold answers keyed by instance id."""
    gold: dict[str, list[str]] = {}
    with open(dataset_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            gold[item["id"]] = item.get("answers", [])
    return gold


def find_agent_outputs(jobs_dir: Path) -> dict[str, str]:
    """Scan Harbor jobs directory for agent output files, returning id -> output text."""
    outputs: dict[str, str] = {}

    for output_file in jobs_dir.rglob("output.txt"):
        trial_dir = output_file.parent
        if trial_dir.name in ("agent", "verifier"):
            trial_dir = trial_dir.parent
        instance_id = re.sub(r"__[A-Za-z0-9]{7}$", "", trial_dir.name)
        if instance_id:
            outputs[instance_id] = output_file.read_text()

    if not outputs:
        for log_file in jobs_dir.rglob("factory-ceo.txt"):
            trial_dir = log_file.parent
            if trial_dir.name in ("agent", "verifier"):
                trial_dir = trial_dir.parent
            instance_id = re.sub(r"__[A-Za-z0-9]{7}$", "", trial_dir.name)
            if instance_id:
                outputs[instance_id] = log_file.read_text()

    return outputs


def evaluate(
    jobs_dir: Path,
    dataset_path: Path,
    output_path: Path | None = None,
) -> dict:
    gold_answers = load_gold_answers(dataset_path)
    agent_outputs = find_agent_outputs(jobs_dir)

    results: list[dict] = []
    total_em = 0.0
    total_f1 = 0.0

    for instance_id, gold in gold_answers.items():
        agent_text = agent_outputs.get(instance_id, "")
        prediction = extract_answer(agent_text) if agent_text else ""
        scores = score_prediction(prediction, gold)

        total_em += scores["exact_match"]
        total_f1 += scores["f1"]

        results.append({
            "instance_id": instance_id,
            "prediction": prediction,
            "gold_answers": gold,
            "exact_match": scores["exact_match"],
            "f1": scores["f1"],
            "resolved": scores["exact_match"] > 0,
        })

    n = max(len(gold_answers), 1)
    summary = {
        "benchmark": "searchqa",
        "total": len(gold_answers),
        "attempted": len(agent_outputs),
        "avg_exact_match": round(total_em / n, 4),
        "avg_f1": round(total_f1 / n, 4),
        "tasks": results,
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2))

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate SearchQA agent outputs")
    parser.add_argument("jobs_dir", type=Path, help="Harbor jobs directory")
    parser.add_argument("--dataset", type=Path, required=True, help="Path to searchqa.jsonl")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path")
    args = parser.parse_args()

    summary = evaluate(args.jobs_dir, args.dataset, args.output)
    print(json.dumps(summary, indent=2))
    print(f"\nEM: {summary['avg_exact_match']:.2%}  F1: {summary['avg_f1']:.2%}")


if __name__ == "__main__":
    main()
