"""Abstract base class for per-benchmark environment adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from factory.skillopt.types import RawPatch, RolloutResult


class EnvAdapter(ABC):

    def setup(self, cfg: dict) -> None:
        pass

    @abstractmethod
    def build_train_env(self, batch_size: int, seed: int) -> Any:
        ...

    @abstractmethod
    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        ...

    @abstractmethod
    def rollout(
        self, env_manager: Any, skill_content: str, out_dir: str,
    ) -> list[RolloutResult]:
        ...

    def reflect(
        self,
        results: list[RolloutResult],
        skill_content: str,
        out_dir: str,
        **kwargs: Any,
    ) -> list[RawPatch]:
        from factory.skillopt.reflect import run_minibatch_reflect

        return run_minibatch_reflect(
            results=results,
            skill_content=skill_content,
            minibatch_size=kwargs.get("minibatch_size", 4),
            edit_budget=kwargs.get("edit_budget", 5),
            workers=kwargs.get("workers", 4),
            step_buffer_context=kwargs.get("step_buffer_context", ""),
        )

    @abstractmethod
    def get_task_types(self) -> list[str]:
        ...
