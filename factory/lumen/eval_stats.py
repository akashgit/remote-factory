#!/usr/bin/env python3
"""LUMEN Eval Stats — aggregate sm_rollouts + fm_rollouts into unified evaluation_results.json.

Reads:
  - .factory/lumen/.running/config.json
  - .factory/lumen/.running/state.json
  - .factory/lumen/.running/iteration_N/sm_rollouts.jsonl (required)
  - .factory/lumen/.running/iteration_N/fm_rollouts.jsonl (optional)

Writes:
  - .factory/lumen/.running/iteration_N/evaluation_results.json

Output structure:
{
  "iteration": N,
  "sm": {
    "num_rollouts": int,
    "scores": [float, ...],
    "best_score": float,
    "best_rollout_idx": int,
    "best_solution": {...},
    "mean_score": float,
    "std_score": float,
    "per_prompt_stats": [...]
  },
  "fm": {...} or null,
  "overall": {
    "num_rollouts": int,
    "scores": [float, ...],
    "best_score": float,
    "best_source": "sm" or "fm",
    "best_rollout_idx": int,
    "best_solution": {...},
    "mean_score": float,
    "std_score": float
  }
}
"""

import json
import sys
from pathlib import Path

import numpy as np


def compute_stats(rollouts: list[dict], prompts_data: dict, source: str) -> dict:
    """Compute statistics for a list of rollouts."""
    scores = [r["score"] for r in rollouts]

    if not scores:
        return {
            "num_rollouts": 0,
            "scores": [],
            "best_score": 0.0,
            "best_rollout_idx": 0,
            "best_solution": {},
            "mean_score": 0.0,
            "std_score": 0.0,
        }

    scoring_direction = prompts_data.get("scoring_direction", "maximize")

    if scoring_direction == "minimize":
        # For minimize: 0.0 means evaluation failure, not a perfect score.
        # Find best among successful rollouts only.
        valid = [(i, s) for i, s in enumerate(scores) if s > 0]
        best_idx = min(valid, key=lambda x: x[1])[0] if valid else 0
    else:
        best_idx = int(np.argmax(scores))

    stats = {
        "num_rollouts": len(rollouts),
        "scores": scores,
        "best_score": float(scores[best_idx]),
        "best_rollout_idx": best_idx,
        "best_solution": rollouts[best_idx].get("solution", {}),
        "mean_score": float(np.mean(scores)),
        "std_score": float(np.std(scores)),
    }

    # Compute per_prompt_stats if rollouts have prompt_idx
    if rollouts and "prompt_idx" in rollouts[0]:
        prompts = prompts_data.get("prompts", [])
        num_prompts = len(prompts)

        # Group by prompt_idx
        groups: dict[int, list[float]] = {}
        for r in rollouts:
            idx = r.get("prompt_idx", 0)
            if idx not in groups:
                groups[idx] = []
            groups[idx].append(r["score"])

        per_prompt_stats = []
        for i in range(num_prompts):
            if i not in groups:
                continue
            group_scores = groups[i]
            strategy = prompts[i].get("strategy", "") if i < len(prompts) else ""
            if scoring_direction == "minimize":
                valid_group = [s for s in group_scores if s > 0]
                group_best = float(min(valid_group)) if valid_group else 0.0
            else:
                group_best = float(max(group_scores))
            per_prompt_stats.append({
                "prompt_idx": i,
                "strategy": strategy,
                "mean": float(np.mean(group_scores)),
                "std": float(np.std(group_scores)),
                "best": group_best,
            })

        stats["per_prompt_stats"] = per_prompt_stats

    return stats


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Aggregate eval stats")
    parser.add_argument("--run-dir", default=None, help="Run directory path")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else Path(".factory/lumen/.running")
    config_path = run_dir / "config.json"
    state_path = run_dir / "state.json"

    if not config_path.exists():
        print(f"ERROR: {config_path} not found", file=sys.stderr)
        sys.exit(1)

    if not state_path.exists():
        print(f"ERROR: {state_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        cfg = json.load(f)

    with open(state_path) as f:
        state = json.load(f)

    iteration = state["iteration"]
    iteration_dir = run_dir / f"iteration_{iteration}"

    # Read prompts.json for per_prompt_stats
    prompts_file = iteration_dir / "prompts.json"
    prompts_data = {}
    if prompts_file.exists():
        with open(prompts_file) as f:
            prompts_data = json.load(f)

    # Read sm_rollouts.jsonl (required)
    sm_file = iteration_dir / "sm_rollouts.jsonl"
    if not sm_file.exists():
        print(f"ERROR: {sm_file} not found", file=sys.stderr)
        sys.exit(1)

    sm_rollouts = []
    with open(sm_file) as f:
        for line in f:
            sm_rollouts.append(json.loads(line))

    print(f"Loaded {len(sm_rollouts)} sm_rollouts")

    # Read fm_rollouts.jsonl (optional)
    fm_file = iteration_dir / "fm_rollouts.jsonl"
    fm_rollouts = []
    if fm_file.exists():
        with open(fm_file) as f:
            for line in f:
                fm_rollouts.append(json.loads(line))
        print(f"Loaded {len(fm_rollouts)} fm_rollouts")
    else:
        print("No fm_rollouts.jsonl found (skipping)")

    # Compute stats
    sm_stats = compute_stats(sm_rollouts, prompts_data, "sm")
    fm_stats = compute_stats(fm_rollouts, prompts_data, "fm") if fm_rollouts else None

    # Compute overall stats
    all_rollouts = sm_rollouts + fm_rollouts
    all_scores = sm_stats["scores"] + (fm_stats["scores"] if fm_stats else [])

    if not all_scores:
        print("ERROR: No rollouts found", file=sys.stderr)
        sys.exit(1)

    scoring_direction = prompts_data.get("scoring_direction", "maximize")
    if scoring_direction == "minimize":
        valid = [(i, s) for i, s in enumerate(all_scores) if s > 0]
        overall_best_idx = min(valid, key=lambda x: x[1])[0] if valid else 0
    else:
        overall_best_idx = int(np.argmax(all_scores))

    # Determine best_source
    if overall_best_idx < len(sm_rollouts):
        best_source = "sm"
        best_solution = sm_rollouts[overall_best_idx].get("solution", {})
    else:
        best_source = "fm"
        best_solution = fm_rollouts[overall_best_idx - len(sm_rollouts)].get("solution", {})

    overall_stats = {
        "num_rollouts": len(all_rollouts),
        "scores": all_scores,
        "best_score": float(all_scores[overall_best_idx]),
        "best_source": best_source,
        "best_rollout_idx": overall_best_idx,
        "best_solution": best_solution,
        "mean_score": float(np.mean(all_scores)),
        "std_score": float(np.std(all_scores)),
    }

    # Build final result
    result = {
        "iteration": iteration,
        "sm": sm_stats,
        "fm": fm_stats,
        "overall": overall_stats,
    }

    # Write evaluation_results.json
    output_file = iteration_dir / "evaluation_results.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nEvaluation results saved to {output_file}")
    print(f"  SM:      best={sm_stats['best_score']:.6f}, mean={sm_stats['mean_score']:.6f}")
    if fm_stats:
        print(f"  FM:      best={fm_stats['best_score']:.6f}, mean={fm_stats['mean_score']:.6f}")
    print(f"  Overall: best={overall_stats['best_score']:.6f} ({best_source}), mean={overall_stats['mean_score']:.6f}")


if __name__ == "__main__":
    main()
