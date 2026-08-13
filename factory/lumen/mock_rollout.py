"""Mock rollout generator for testing workflow without vLLM."""

import random
from typing import Any


def generate_mock_rollouts(
    prompts: list[dict[str, Any]], num_per_prompt: int,
) -> list[dict[str, Any]]:
    """Generate mock rollouts (random solutions) for testing.

    Args:
        prompts: List of prompt dicts from prompts.json
        num_per_prompt: Number of rollouts to generate per prompt

    Returns:
        List of rollout dicts
    """
    all_rollouts = []

    for prompt_idx, prompt in enumerate(prompts):
        for rollout_idx in range(num_per_prompt):
            # Generate a random solution
            # For circle packing: random circles
            solution = generate_random_circle_solution(n_circles=26)

            rollout = {
                "prompt_idx": prompt_idx,
                "rollout_idx": rollout_idx,
                "global_idx": prompt_idx * num_per_prompt + rollout_idx,
                "prompt": prompt["prompt_text"],
                "solution": solution,
                "thinking": f"Mock thinking for rollout {rollout_idx}",
                "code": "# Mock code",
            }
            all_rollouts.append(rollout)

    return all_rollouts


def generate_random_circle_solution(n_circles: int = 26) -> dict[str, list[list[float]]]:
    """Generate a random circle packing solution.

    Args:
        n_circles: Number of circles to generate

    Returns:
        Solution dict with "circles" key
    """
    circles = []
    for _ in range(n_circles):
        # Random position
        x = random.uniform(0.1, 0.9)
        y = random.uniform(0.1, 0.9)

        # Small random radius (to avoid overlap issues in mock)
        r = random.uniform(0.02, 0.08)

        circles.append([x, y, r])

    return {"circles": circles}
