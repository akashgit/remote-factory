"""Rollout generator using a real vLLM server."""

from __future__ import annotations

from typing import Any

from factory.rl.solution_parser import parse_solution
from factory.rl.vllm_client import VLLMClient


def generate_vllm_rollouts(
    prompts: list[dict[str, Any]],
    num_per_prompt: int,
    *,
    vllm_url: str = "http://localhost:8000/v1",
    vllm_model: str = "meta-llama/Llama-3.1-8B-Instruct",
    api_key: str = "EMPTY",
    temperature: float = 0.8,
    max_tokens: int = 4096,
    task_type: str = "circle-packing",
) -> list[dict[str, Any]]:
    """Generate rollouts via a vLLM server.

    Returns a list of rollout dicts in the same shape as mock_rollout.py.
    On per-prompt failure, emits penalty rollouts instead of aborting.
    """
    all_rollouts: list[dict[str, Any]] = []

    with VLLMClient(
        base_url=vllm_url,
        api_key=api_key,
        model=vllm_model,
    ) as client:
        for prompt_idx, prompt in enumerate(prompts):
            prompt_text = prompt["prompt_text"]
            results = client.generate(
                prompt_text,
                n=num_per_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            rollouts_for_prompt = _results_to_rollouts(
                results,
                prompt_idx=prompt_idx,
                num_per_prompt=num_per_prompt,
                prompt_text=prompt_text,
                task_type=task_type,
            )
            all_rollouts.extend(rollouts_for_prompt)

    return all_rollouts


def _results_to_rollouts(
    results: list,
    *,
    prompt_idx: int,
    num_per_prompt: int,
    prompt_text: str,
    task_type: str,
) -> list[dict[str, Any]]:
    """Convert VLLMClient results into rollout dicts.

    Pads with penalty rollouts if fewer results than expected.
    """
    rollouts: list[dict[str, Any]] = []

    for rollout_idx in range(num_per_prompt):
        global_idx = prompt_idx * num_per_prompt + rollout_idx

        if rollout_idx < len(results) and results[rollout_idx].success:
            raw_text = results[rollout_idx].text
            solution = parse_solution(raw_text, task_type)
            if solution is None:
                print(
                    f"WARNING: Failed to parse solution for prompt {prompt_idx} "
                    f"rollout {rollout_idx}, using empty solution"
                )
                solution = {}

            rollouts.append({
                "prompt_idx": prompt_idx,
                "rollout_idx": rollout_idx,
                "global_idx": global_idx,
                "prompt": prompt_text,
                "solution": solution,
                "thinking": raw_text,
                "code": "",
            })
        else:
            error_msg = "no result"
            if rollout_idx < len(results):
                error_msg = results[rollout_idx].error or "unknown error"
            print(
                f"WARNING: Rollout failed for prompt {prompt_idx} "
                f"rollout {rollout_idx}: {error_msg}"
            )
            rollouts.append({
                "prompt_idx": prompt_idx,
                "rollout_idx": rollout_idx,
                "global_idx": global_idx,
                "prompt": prompt_text,
                "solution": {},
                "thinking": "",
                "code": "",
            })

    return rollouts
