"""FeatureBench adapter — not yet implemented."""
from __future__ import annotations

from factory.skillopt.adapter import EnvAdapter


class FeaturebenchAdapter(EnvAdapter):

    def build_train_env(self, batch_size: int, seed: int):
        raise NotImplementedError("FeaturebenchAdapter not yet implemented")

    def build_eval_env(self, env_num: int, split: str, seed: int):
        raise NotImplementedError("FeaturebenchAdapter not yet implemented")

    def rollout(self, env_manager, skill_content: str, out_dir: str):
        raise NotImplementedError("FeaturebenchAdapter not yet implemented")

    def get_task_types(self) -> list[str]:
        raise NotImplementedError("FeaturebenchAdapter not yet implemented")
