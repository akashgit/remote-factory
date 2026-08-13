#!/usr/bin/env python3
"""Einstein Arena RL Training — reads all parameters from a run config file.

Usage:
    python3 -m factory.lumen.train --config .factory/lumen/current_run/config.json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def main() -> None:
    """Main entry point for RL training."""
    parser = argparse.ArgumentParser(description="Einstein Arena RL Training (MVP)")
    parser.add_argument("--config", required=True, help="Path to resolved run config.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        cfg = json.load(f)

    task_name = cfg["task_name"]
    task_dir = Path(cfg["task_dir"])
    project_path = config_path.parents[2]  # .factory/lumen/run-NNN/config.json → project root
    mock = cfg.get("mock", False)
    model_path = cfg.get("model_path", "Qwen/Qwen3-8B")
    num_rollouts_per_prompt = cfg.get("num_rollouts_per_prompt", 64)

    # Read current iteration from state.json (in same run directory)
    run_dir = config_path.parent
    state_path = run_dir / "state.json"
    with open(state_path) as f:
        state = json.load(f)
    iteration = state["iteration"]

    iteration_dir = run_dir / f"iteration_{iteration}"
    iteration_dir.mkdir(parents=True, exist_ok=True)

    print("=== Einstein Arena RL Training (MVP) ===")
    print(f"Task: {task_name}")
    print(f"Iteration: {iteration}")
    print(f"Rollouts per prompt: {num_rollouts_per_prompt}")
    print(f"Mode: {'MOCK' if mock else 'REAL'}")
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
    if mock:
        from factory.lumen.mock_rollout import generate_mock_rollouts

        all_rollouts = generate_mock_rollouts(prompts, num_rollouts_per_prompt)
    else:
        import subprocess
        cmd = [
            sys.executable, "-m", "factory.lumen.run_verl",
            "--prompts", str(prompts_file),
            "--task-dir", str(task_dir),
            "--checkpoint-dir", str(run_dir / "checkpoint"),
            "--output-dir", str(iteration_dir),
            "--model-path", model_path,
            "--iteration", str(iteration),
            "--rollouts-per-prompt", str(num_rollouts_per_prompt),
            "--num-gpus", str(cfg.get("num_gpus", 8)),
            "--rollout-tp", str(cfg.get("rollout_tp", 4)),
            "--lora-rank", str(cfg.get("lora_rank", 32)),
            "--learning-rate", str(cfg.get("learning_rate", 4e-5)),
            "--kl-coef", str(cfg.get("kl_coef", 0.1)),
            "--temperature", str(cfg.get("temperature", 1.0)),
            "--phase1-max-tokens", str(cfg.get("phase1_max_tokens", 26000)),
            "--eval-timeout", str(cfg.get("eval_timeout", 530)),
            "--groups-per-batch", str(cfg.get("groups_per_batch", 8)),
        ]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"VERL training failed with exit code {result.returncode}")
            sys.exit(result.returncode)
        return

    print(f"Generated {len(all_rollouts)} rollouts")

    # 3. Evaluate rollouts
    from factory.lumen.evaluate import evaluate_rollouts

    scoring_direction = prompts_data["scoring_direction"]
    reward_cfg = cfg.get("reward", None)
    eval_results = evaluate_rollouts(
        all_rollouts, task_dir,
        direction=scoring_direction, reward_cfg=reward_cfg,
    )

    raw_scores = [r["raw_score"] for r in eval_results]
    scores = [r["score"] for r in eval_results]

    for rollout, raw, shaped in zip(all_rollouts, raw_scores, scores):
        rollout["raw_score"] = raw
        rollout["score"] = shaped

    print(f"Evaluated {len(scores)} solutions")

    # 4. Find best (by raw score — the verifier's actual metric)
    if scoring_direction == "maximize":
        best_idx = int(np.argmax(raw_scores))
    else:
        best_idx = int(np.argmin(raw_scores))

    # 5. Compute per-prompt stats
    per_prompt_stats = []
    num_prompts = len(prompts)
    for i in range(num_prompts):
        start = i * num_rollouts_per_prompt
        end = start + num_rollouts_per_prompt
        prompt_raw = raw_scores[start:end]
        prompt_shaped = scores[start:end]

        per_prompt_stats.append(
            {
                "prompt_idx": i,
                "strategy": prompts[i]["strategy"],
                "mean_raw": float(np.mean(prompt_raw)),
                "mean_reward": float(np.mean(prompt_shaped)),
                "std": float(np.std(prompt_raw)),
                "best": float(
                    max(prompt_raw) if scoring_direction == "maximize" else min(prompt_raw)
                ),
            }
        )

    # 6. Save results
    results = {
        "iteration": iteration,
        "num_rollouts": len(all_rollouts),
        "raw_scores": raw_scores,
        "scores": scores,
        "best_raw_score": raw_scores[best_idx],
        "best_score": scores[best_idx],
        "best_rollout_idx": best_idx,
        "best_solution": all_rollouts[best_idx]["solution"],
        "mean_raw_score": float(np.mean(raw_scores)),
        "mean_score": float(np.mean(scores)),
        "std_score": float(np.std(raw_scores)),
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
    print("Results saved:")
    print(f"  - {results_file}")
    print(f"  - {rollouts_file}")
    print(f"  - Best score: {results['best_score']:.6f}")
    print(f"  - Mean score: {results['mean_score']:.6f}")


if __name__ == "__main__":
    main()
