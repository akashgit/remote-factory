#!/usr/bin/env python3
"""LUMEN RL Training — reads all parameters from a run config file.

LUMEN: Learning-based Universal Modeling and Evolution eNgine
RL training system for scientific discovery tasks.

Usage:
    python3 -m factory.lumen.train --config .factory/lumen/run_YYYYMMDD-HHMMSS/config.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


def cleanup_gpu_processes():
    """Clean up any lingering Ray/vLLM processes from previous runs."""
    import os
    import signal

    try:
        # Find Ray and vLLM processes owned by current user
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "ray::"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        # Clean up vLLM processes
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "VLLM"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        print("✓ GPU processes cleaned")
    except Exception as e:
        print(f"Warning: GPU cleanup failed: {e}")


def main() -> None:
    """Main entry point for RL training."""
    # Clean up any lingering GPU processes from previous runs
    cleanup_gpu_processes()

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
    project_path = config_path.parents[3]  # .factory/lumen/run_YYYYMMDD-HHMMSS/config.json → project root
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
        import os
        import subprocess

        # CRITICAL: Use current Python (should be from lumen venv)
        # Set PYTHONPATH so subprocess can import factory.lumen modules
        python_exe = sys.executable

        # Add remote-factory root to PYTHONPATH for factory.lumen imports
        factory_root = Path(__file__).resolve().parents[2]  # factory/lumen/train.py → remote-factory/
        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{factory_root}:{current_pythonpath}" if current_pythonpath else str(factory_root)

        cmd = [
            python_exe, "-m", "factory.lumen.run_verl",
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
        result = subprocess.run(cmd, env=env, check=False)
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

    # 4. Save sm_rollouts.jsonl (small model rollouts)
    # Note: eval_stats.py will read this and generate evaluation_results.json
    rollouts_file = iteration_dir / "sm_rollouts.jsonl"
    num_prompts = len(prompts)
    with open(rollouts_file, "w") as f:
        for i, rollout in enumerate(all_rollouts):
            prompt_idx = i // num_rollouts_per_prompt if num_rollouts_per_prompt > 0 else 0
            rollout_idx = i % num_rollouts_per_prompt if num_rollouts_per_prompt > 0 else i
            record = {
                "prompt_idx": prompt_idx,
                "rollout_idx": rollout_idx,
                "global_idx": i,
                "prompt": prompts[prompt_idx].get("prompt_text", "") if prompt_idx < num_prompts else "",
                "thinking": rollout.get("thinking", ""),
                "code": rollout.get("code", ""),
                "solution": rollout.get("solution", {}),
                "score": rollout["score"],
                "gen_case": rollout.get("gen_case", "mock"),
                "p1_len": rollout.get("p1_len", 0),
                "p2_len": rollout.get("p2_len", 0),
            }
            f.write(json.dumps(record) + "\n")

    print()
    print(f"Results saved to {rollouts_file}")
    print(f"  - Best score: {max(scores):.6f}")
    print(f"  - Mean score: {float(np.mean(scores)):.6f}")

    # Clean up GPU processes after successful completion
    cleanup_gpu_processes()


if __name__ == "__main__":
    try:
        main()
    finally:
        # Ensure cleanup even on failure
        cleanup_gpu_processes()
