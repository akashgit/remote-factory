"""Reward shaping — convert raw verifier scores to RL training rewards.

Raw verifier scores vary wildly across Einstein Arena tasks: circle-packing
returns ~0-3 (MAXIMIZE), thomson-problem returns ~30000+ (MINIMIZE),
flat-polynomials returns ~1-10 (MINIMIZE). RL training (PPO) needs
rewards in a stable, comparable range.

Each task can specify reward config in its config.json:

    "reward": {
        "type": "linear",         # shaping function
        "baseline": 2.5,          # known baseline score
        "scale": 1.0,             # scaling factor
        "clip_min": -1.0,         # floor
        "clip_max": 1.0           # ceiling
    }

If no reward config is present, the default "identity" shaping is used
(raw score passed through, same as before).
"""

from __future__ import annotations

import math


def shape_reward(
    raw_score: float,
    direction: str,
    reward_cfg: dict | None = None,
) -> float:
    """Convert a raw verifier score to an RL reward.

    Args:
        raw_score: Raw score from the verifier's evaluate().
        direction: "maximize" or "minimize".
        reward_cfg: Per-task reward shaping config (from config.json "reward" key).
            If None, returns raw_score unchanged.

    Returns:
        Shaped reward (float).
    """
    if not math.isfinite(raw_score):
        return -1.0

    if reward_cfg is None:
        return raw_score

    shaping_type = reward_cfg.get("type", "identity")

    if shaping_type == "identity":
        return raw_score

    if shaping_type == "linear":
        return _linear(raw_score, direction, reward_cfg)

    if shaping_type == "binary":
        return _binary(raw_score, direction, reward_cfg)

    if shaping_type == "relative":
        return _relative(raw_score, direction, reward_cfg)

    if shaping_type == "reciprocal":
        return _reciprocal(raw_score, reward_cfg)

    return raw_score


def _linear(raw_score: float, direction: str, cfg: dict) -> float:
    """Linear scaling: reward = (score - baseline) * scale, clipped.

    For MINIMIZE tasks, the sign is flipped so that lower raw scores
    produce higher rewards.
    """
    baseline = cfg.get("baseline", 0.0)
    scale = cfg.get("scale", 1.0)
    clip_min = cfg.get("clip_min", -1.0)
    clip_max = cfg.get("clip_max", 1.0)

    delta = raw_score - baseline
    if direction.lower() == "minimize":
        delta = -delta

    reward = delta * scale
    return max(clip_min, min(clip_max, reward))


def _binary(raw_score: float, direction: str, cfg: dict) -> float:
    """Binary reward: 1.0 if score beats threshold, 0.0 otherwise."""
    threshold = cfg.get("threshold", 0.0)

    if direction.lower() == "maximize":
        return 1.0 if raw_score > threshold else 0.0
    else:
        return 1.0 if raw_score < threshold else 0.0


def _reciprocal(raw_score: float, cfg: dict) -> float:
    """Reciprocal reward: reward = scale / (epsilon + raw_score).

    The standard Discover transform for MINIMIZE tasks: smaller raw scores
    produce larger rewards, naturally yielding positive values without
    needing a baseline or sign flip.
    """
    scale = cfg.get("scale", 1.0)
    epsilon = cfg.get("epsilon", 1e-8)

    if raw_score < 0:
        return 0.0

    return scale / (epsilon + raw_score)


def _relative(raw_score: float, direction: str, cfg: dict) -> float:
    """Relative improvement: reward = (score - baseline) / |baseline|, clipped.

    Produces a percentage-improvement reward. Useful when tasks have
    very different absolute score ranges.
    """
    baseline = cfg.get("baseline", 1.0)
    clip_min = cfg.get("clip_min", -1.0)
    clip_max = cfg.get("clip_max", 1.0)

    if abs(baseline) < 1e-12:
        return 0.0

    delta = (raw_score - baseline) / abs(baseline)
    if direction.lower() == "minimize":
        delta = -delta

    return max(clip_min, min(clip_max, delta))
