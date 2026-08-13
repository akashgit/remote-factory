"""Type definitions for RL training."""

from typing import TypedDict


class Rollout(TypedDict):
    """A single rollout generated from a prompt."""

    prompt_idx: int
    rollout_idx: int
    global_idx: int
    prompt: str
    solution: dict
    thinking: str
    code: str


class TrainingMetrics(TypedDict, total=False):
    """Metrics from one RL training iteration."""

    loss: float
    advantage_mean: float
    advantage_std: float
    kl_divergence: float
    beta_solved: float


class RolloutRecord(TypedDict):
    """A single rollout record for rollouts.jsonl."""

    prompt_idx: int
    rollout_idx: int
    global_idx: int
    prompt: str
    thinking: str
    code: str
    solution: dict
    score: float
    gen_case: str
    p1_len: int
    p2_len: int
