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


