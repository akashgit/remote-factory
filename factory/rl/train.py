#!/usr/bin/env python3
"""Einstein Arena RL Training - MVP version with mock rollouts."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def main() -> None:
    """Main entry point for RL training."""
    parser = argparse.ArgumentParser(description="Einstein Arena RL Training (MVP)")
    parser.add_argument("--task", required=True, help="Task name (e.g., circle-packing)")
    parser.add_argument("--task-dir", required=True, help="Harbor task directory")
    parser.add_argument("--project-path", required=True, help="Project root path")
    parser.add_argument("--iteration", type=int, required=True, help="Current iteration (0-based)")
    parser.add_argument(
        "--num-rollouts-per-prompt",
        type=int,
        default=8,
        help="Rollouts per prompt (default: 8, production: 64)",
    )
    parser.add_argument(
        "--mock", action=argparse.BooleanOptionalAction, default=True,
        help="Use mock rollouts (default: True, use --no-mock for real vLLM)",
    )
    parser.add_argument(
        "--vllm-url", default="http://localhost:8000/v1",
        help="vLLM server base URL (default: http://localhost:8000/v1)",
    )
    parser.add_argument(
        "--vllm-model", default="meta-llama/Llama-3.1-8B-Instruct",
        help="Model name on the vLLM server",
    )
    parser.add_argument(
        "--api-key", default="EMPTY",
        help="API key for vLLM server (default: EMPTY)",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.8,
        help="Sampling temperature (default: 0.8)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=4096,
        help="Max tokens per completion (default: 4096)",
    )

    args = parser.parse_args()

    project_path = Path(args.project_path)
    iteration_dir = project_path / ".factory/rl" / f"iteration_{args.iteration}"
    iteration_dir.mkdir(parents=True, exist_ok=True)

    print("=== Einstein Arena RL Training (MVP) ===")
    print(f"Task: {args.task}")
    print(f"Iteration: {args.iteration}")
    print(f"Rollouts per prompt: {args.num_rollouts_per_prompt}")
    print(f"Mode: {'MOCK' if args.mock else 'REAL'}")
    print()

    # 1. Load prompts
    prompts_file = iteration_dir / "prompts.json"
    if not prompts_file.exists():
        print(f"ERROR: {prompts_file} not found")
        sys.exit(1)

    with open(prompts_file) as f:
        prompts_data = json.load(f)

    prompts = prompts_data["prompts"]
    print(f"Loaded {len(prompts)} prompts")

    # 2. Generate rollouts
    if args.mock:
        from factory.rl.mock_rollout import generate_mock_rollouts

        all_rollouts = generate_mock_rollouts(prompts, args.num_rollouts_per_prompt)
    else:
        from factory.rl.vllm_client import VLLMClient
        from factory.rl.vllm_rollout import generate_vllm_rollouts

        client = VLLMClient(base_url=args.vllm_url, model=args.vllm_model)
        if not client.is_available():
            print(f"ERROR: vLLM server not reachable at {args.vllm_url}")
            print("Start a vLLM server or use --mock for testing")
            client.close()
            sys.exit(1)
        client.close()

        all_rollouts = generate_vllm_rollouts(
            prompts,
            args.num_rollouts_per_prompt,
            vllm_url=args.vllm_url,
            vllm_model=args.vllm_model,
            api_key=args.api_key,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            task_type=args.task,
        )

    print(f"Generated {len(all_rollouts)} rollouts")

    # 3. Evaluate rollouts
    from factory.rl.evaluate import evaluate_rollouts

    scores = evaluate_rollouts(all_rollouts, Path(args.task_dir))

    print(f"Evaluated {len(scores)} solutions")

    # 4. Find best
    scoring_direction = prompts_data["scoring_direction"]
    if scoring_direction == "maximize":
        best_idx = int(np.argmax(scores))
    else:
        best_idx = int(np.argmin(scores))

    # 5. Compute per-prompt stats
    per_prompt_stats = []
    num_prompts = len(prompts)
    for i in range(num_prompts):
        start = i * args.num_rollouts_per_prompt
        end = start + args.num_rollouts_per_prompt
        prompt_scores = scores[start:end]

        per_prompt_stats.append(
            {
                "prompt_idx": i,
                "strategy": prompts[i]["strategy"],
                "mean": float(np.mean(prompt_scores)),
                "std": float(np.std(prompt_scores)),
                "best": float(max(prompt_scores) if scoring_direction == "maximize" else min(prompt_scores)),
            }
        )

    # 6. Save results
    results = {
        "iteration": args.iteration,
        "num_rollouts": len(all_rollouts),
        "scores": scores,
        "best_score": scores[best_idx],
        "best_rollout_idx": best_idx,
        "best_solution": all_rollouts[best_idx]["solution"],
        "mean_score": float(np.mean(scores)),
        "std_score": float(np.std(scores)),
        "per_prompt_stats": per_prompt_stats,
    }

    results_file = iteration_dir / "evaluation_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    # Save rollouts
    rollouts_file = iteration_dir / "rollouts.jsonl"
    with open(rollouts_file, "w") as f:
        for rollout in all_rollouts:
            f.write(json.dumps(rollout) + "\n")

    print()
    print("✓ Results saved:")
    print(f"  - {results_file}")
    print(f"  - {rollouts_file}")
    print(f"  - Best score: {results['best_score']:.6f}")
    print(f"  - Mean score: {results['mean_score']:.6f}")


if __name__ == "__main__":
    main()
