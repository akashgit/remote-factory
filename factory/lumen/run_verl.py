"""LUMEN VERL launch wrapper — orchestrate a single RL training iteration.

LUMEN: Learning-based Universal Modeling and Evolution eNgine
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def build_verl_overrides(args: argparse.Namespace, output_dir: Path) -> list[str]:
    """Generate VERL Hydra override list from parsed CLI args."""
    checkpoint_dir = Path(args.checkpoint_dir)
    latest_ckpt = checkpoint_dir / "latest"

    if args.iteration > 0 and latest_ckpt.exists():
        resume_mode = "resume_path"
        resume_path = str(latest_ckpt)
    else:
        resume_mode = "auto"
        resume_path = ""

    groups_per_batch = args.groups_per_batch
    ppo_mini_batch = args.rollouts_per_prompt * groups_per_batch

    overrides = [
        "algorithm.adv_estimator=entropic_adaptive_beta",
        "algorithm.use_kl_in_reward=False",
        f"algorithm.kl_ctrl.kl_coef={args.kl_coef}",
        f"data.train_files={args.parquet_path}",
        f"data.val_files={args.parquet_path}",
        f"data.train_batch_size={groups_per_batch}",
        "data.max_prompt_length=4096",
        "data.max_response_length=28672",
        "data.filter_overlong_prompts=True",
        "data.truncation=error",
        f"actor_rollout_ref.model.path={args.model_path}",
        "actor_rollout_ref.model.use_remove_padding=True",
        "actor_rollout_ref.model.enable_gradient_checkpointing=True",
        f"actor_rollout_ref.model.lora_rank={args.lora_rank}",
        f"actor_rollout_ref.model.lora_alpha={args.lora_rank}",
        "actor_rollout_ref.model.target_modules=all-linear",
        "++actor_rollout_ref.model.lora.merge=True",
        f"actor_rollout_ref.actor.optim.lr={args.learning_rate}",
        "actor_rollout_ref.actor.optim.betas=[0.9,0.95]",
        "actor_rollout_ref.actor.grad_clip=1.0",
        f"actor_rollout_ref.actor.ppo_mini_batch_size={ppo_mini_batch}",
        "actor_rollout_ref.actor.clip_ratio=1000.0",
        "actor_rollout_ref.actor.use_dynamic_bsz=True",
        "actor_rollout_ref.actor.ppo_max_token_len_per_gpu=32768",
        "actor_rollout_ref.actor.use_kl_loss=False",
        "actor_rollout_ref.actor.entropy_coeff=0",
        "actor_rollout_ref.actor.fsdp_config.param_offload=True",
        "actor_rollout_ref.actor.fsdp_config.optimizer_offload=True",
        "actor_rollout_ref.actor.fsdp_config.ulysses_sequence_parallel_size=1",
        "actor_rollout_ref.rollout.name=vllm",
        f"actor_rollout_ref.rollout.tensor_model_parallel_size={args.rollout_tp}",
        "actor_rollout_ref.rollout.gpu_memory_utilization=0.5",
        f"actor_rollout_ref.rollout.n={args.rollouts_per_prompt}",
        "actor_rollout_ref.rollout.log_prob_use_dynamic_bsz=True",
        "actor_rollout_ref.rollout.log_prob_max_token_len_per_gpu=32768",
        "actor_rollout_ref.rollout.free_cache_engine=True",
        "actor_rollout_ref.rollout.enforce_eager=True",
        "+actor_rollout_ref.rollout.agent.agent_loop_manager_class="
        "factory.lumen.verl_integration.agent_loop.LumenAgentLoopManagerTQ",
        "actor_rollout_ref.ref.log_prob_use_dynamic_bsz=True",
        "actor_rollout_ref.ref.log_prob_max_token_len_per_gpu=32768",
        "actor_rollout_ref.ref.fsdp_config.param_offload=True",
        "actor_rollout_ref.ref.fsdp_config.ulysses_sequence_parallel_size=1",
        "reward.custom_reward_function.path="
        f"{Path(__file__).parent / 'verl_integration/reward.py'}",
        "reward.custom_reward_function.name=compute_score",
        "trainer.balance_batch=True",
        'trainer.logger=["console","file"]',
        "trainer.project_name=lumen",
        "trainer.experiment_name=lumen",
        f"trainer.default_local_dir={args.checkpoint_dir}",
        f"trainer.n_gpus_per_node={args.num_gpus}",
        "trainer.nnodes=1",
        "trainer.save_freq=0",
        "trainer.test_freq=-1",
        "trainer.total_epochs=1",
        f"trainer.rollout_data_dir={args.checkpoint_dir}/rollouts",
        "trainer.val_before_train=False",
        f"trainer.resume_mode={resume_mode}",
    ]

    if resume_path:
        overrides.append(f"trainer.resume_from_path={resume_path}")

    # Hydra: disable .hydra/ directory and log files
    overrides.append(f"hydra.run.dir={output_dir}")
    overrides.append("hydra.output_subdir=null")  # Disable .hydra/ directory
    overrides.append("hydra/job_logging=none")     # Disable main_ppo.log

    return overrides


def post_process_results(
    rollout_log: Path,
    output_dir: Path,
    prompts_data: dict,
    iteration: int,
) -> dict:
    """Read VERL rollout log and write evaluation_results.json + rollouts.jsonl."""
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    with open(rollout_log) as f:
        for line in f:
            entries.append(json.loads(line))

    scores = [e["score"] for e in entries]
    num_rollouts = len(entries)
    num_prompts = len(prompts_data["prompts"])
    rollouts_per_prompt = num_rollouts // num_prompts if num_prompts > 0 else 0
    scoring_direction = prompts_data.get("scoring_direction", "maximize")

    # Write rollouts.jsonl
    with open(output_dir / "rollouts.jsonl", "w") as f:
        for idx, entry in enumerate(entries):
            prompt_idx = idx // rollouts_per_prompt if rollouts_per_prompt > 0 else 0
            rollout_idx = idx % rollouts_per_prompt if rollouts_per_prompt > 0 else idx
            record = {
                "prompt_idx": prompt_idx,
                "rollout_idx": rollout_idx,
                "global_idx": idx,
                "prompt": entry.get("input", ""),
                "thinking": "",
                "code": "",
                "solution": {},
                "score": entry["score"],
                "gen_case": "A",
                "p1_len": 0,
                "p2_len": 0,
            }
            f.write(json.dumps(record) + "\n")

    # Compute per-prompt stats
    per_prompt_stats = []
    for i in range(num_prompts):
        start = i * rollouts_per_prompt
        end = start + rollouts_per_prompt
        group_scores = scores[start:end]
        if not group_scores:
            continue
        strategy = prompts_data["prompts"][i].get("strategy", "")
        best_fn = max if scoring_direction == "maximize" else min
        per_prompt_stats.append({
            "prompt_idx": i,
            "strategy": strategy,
            "mean": float(np.mean(group_scores)),
            "std": float(np.std(group_scores)),
            "best": float(best_fn(group_scores)),
        })

    best_idx = int(np.argmax(scores) if scoring_direction == "maximize" else np.argmin(scores))
    best_solution = entries[best_idx].get("solution", {})

    results = {
        "iteration": iteration,
        "num_rollouts": num_rollouts,
        "scores": scores,
        "best_score": scores[best_idx],
        "best_rollout_idx": best_idx,
        "best_solution": best_solution,
        "mean_score": float(np.mean(scores)),
        "std_score": float(np.std(scores)),
        "per_prompt_stats": per_prompt_stats,
    }

    with open(output_dir / "evaluation_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Lumen VERL Training Launcher")
    parser.add_argument("--prompts", required=True, help="Path to prompts.json")
    parser.add_argument("--task-dir", required=True, help="Einstein Arena task directory")
    parser.add_argument("--checkpoint-dir", required=True, help="VERL checkpoint directory")
    parser.add_argument("--output-dir", required=True, help="Iteration output directory")
    parser.add_argument("--model-path", required=True, help="Base model path")
    parser.add_argument("--iteration", type=int, required=True, help="Current iteration")
    parser.add_argument("--rollouts-per-prompt", type=int, default=64)
    parser.add_argument("--num-gpus", type=int, default=8)
    parser.add_argument("--rollout-tp", type=int, default=4)
    parser.add_argument("--lora-rank", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=4e-5)
    parser.add_argument("--kl-coef", type=float, default=0.1)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--phase1-max-tokens", type=int, default=26000)
    parser.add_argument("--eval-timeout", type=int, default=530)
    parser.add_argument("--groups-per-batch", type=int, default=8)

    args = parser.parse_args()

    prompts_path = Path(args.prompts)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Create parquet from prompts
    from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

    parquet_path = output_dir / "prompts.parquet"
    create_parquet_from_prompts(prompts_path, parquet_path, task_dir=args.task_dir)
    args.parquet_path = str(parquet_path)

    # Step 2: Build VERL config
    overrides = build_verl_overrides(args, output_dir)

    # Step 3: Set environment variables for AgentLoopManager config
    import os
    os.environ["LUMEN_TASK_DIR"] = str(args.task_dir)
    os.environ["LUMEN_PHASE1_MAX_TOKENS"] = str(args.phase1_max_tokens)
    os.environ["LUMEN_EVAL_TIMEOUT"] = str(args.eval_timeout)
    os.environ["LUMEN_MAX_MODEL_LEN"] = "32768"

    # Step 3b: Set FileLogger path to iteration directory
    os.environ["VERL_FILE_LOGGER_PATH"] = str(output_dir / "metrics.jsonl")

    # CUDA runtime: PyTorch and vLLM bundle their own libcudart (CUDA 12.x).
    # Do NOT add nvidia/cu13/lib to LD_LIBRARY_PATH — it contains CUDA 13.x
    # runtime which requires a newer driver than most machines have.
    python_exe = sys.executable

    # Step 4: Launch VERL
    print(f"=== Lumen VERL Training — Iteration {args.iteration} ===")
    print(f"Model: {args.model_path}")
    print(f"GPUs: {args.num_gpus}, TP: {args.rollout_tp}")
    total = args.groups_per_batch * args.rollouts_per_prompt
    print(f"Rollouts: {args.groups_per_batch} × {args.rollouts_per_prompt} = {total}")

    import subprocess
    # CRITICAL: Use current Python (already set above when configuring LD_LIBRARY_PATH)
    cmd = [
        python_exe, "-m", "verl.trainer.main_ppo",
        *overrides,
    ]
    result = subprocess.run(cmd, check=False)

    # Step 4: Post-process results — VERL names files {global_steps}.jsonl (1-based)
    rollout_dir = Path(args.checkpoint_dir) / "rollouts"
    rollout_files = sorted(rollout_dir.glob("*.jsonl")) if rollout_dir.exists() else []

    if result.returncode != 0:
        if rollout_files:
            print(
                f"WARNING: VERL exited with code {result.returncode} but rollout data exists — "
                "likely a benign cleanup error. Continuing post-processing.",
                file=sys.stderr,
            )
        else:
            print(f"VERL training failed with exit code {result.returncode}", file=sys.stderr)
            sys.exit(result.returncode)

    if not rollout_files:
        print(f"WARNING: No rollout logs found in {rollout_dir}", file=sys.stderr)
        sys.exit(1)
    rollout_log = rollout_files[-1]

    with open(prompts_path) as f:
        prompts_data = json.load(f)

    results = post_process_results(rollout_log, output_dir, prompts_data, args.iteration)
    print(f"Best score: {results['best_score']:.6f}")
    print(f"Mean score: {results['mean_score']:.6f}")


if __name__ == "__main__":
    main()
