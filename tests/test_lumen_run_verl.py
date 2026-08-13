"""Tests for Lumen VERL launch wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def mock_prompts(tmp_path: Path) -> Path:
    data = {
        "iteration": 0,
        "scoring_direction": "maximize",
        "prompts": [
            {"prompt_idx": i, "strategy": f"s{i}", "prompt_text": f"prompt {i}"}
            for i in range(8)
        ],
    }
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture()
def mock_rollout_log(tmp_path: Path) -> Path:
    """Create a mock VERL rollout log (512 lines)."""
    log_dir = tmp_path / "rollouts"
    log_dir.mkdir()
    log_path = log_dir / "0.jsonl"
    with open(log_path, "w") as f:
        for prompt_idx in range(8):
            for rollout_idx in range(64):
                global_idx = prompt_idx * 64 + rollout_idx
                entry = {
                    "input": f"prompt {prompt_idx}",
                    "output": f"<think>thinking</think>\n```python\nprint('hello')\n```",
                    "score": 1.0 + prompt_idx * 0.1 + rollout_idx * 0.001,
                    "step": 0,
                    "uid": f"uid_{prompt_idx}_{rollout_idx}_0",
                    "gts": None,
                }
                f.write(json.dumps(entry) + "\n")
    return log_path


class TestBuildVerlOverrides:
    def test_basic_overrides(self, mock_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.run_verl import build_verl_overrides

        import argparse
        args = argparse.Namespace(
            prompts=str(mock_prompts),
            task_dir="/path/to/task",
            checkpoint_dir=str(tmp_path / "ckpt"),
            output_dir=str(tmp_path / "out"),
            model_path="Qwen/Qwen3-8B",
            iteration=0,
            rollouts_per_prompt=64,
            num_gpus=8,
            rollout_tp=4,
            lora_rank=32,
            learning_rate=4e-5,
            kl_coef=0.1,
            temperature=0.8,
            phase1_max_tokens=26000,
            eval_timeout=60,
            parquet_path=str(tmp_path / "prompts.parquet"),
        )
        overrides = build_verl_overrides(args)
        assert any("entropic_adaptive_beta" in o for o in overrides)
        assert any("total_epochs=1" in o for o in overrides)
        assert any("Qwen/Qwen3-8B" in o for o in overrides)

    def test_resume_mode_for_iteration_zero(self, mock_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.run_verl import build_verl_overrides

        import argparse
        args = argparse.Namespace(
            prompts=str(mock_prompts), task_dir=".", checkpoint_dir=str(tmp_path),
            output_dir=str(tmp_path), model_path="m", iteration=0,
            rollouts_per_prompt=64, num_gpus=1, rollout_tp=1, lora_rank=32,
            learning_rate=4e-5, kl_coef=0.1, temperature=0.8,
            phase1_max_tokens=26000, eval_timeout=60,
            parquet_path=str(tmp_path / "p.parquet"),
        )
        overrides = build_verl_overrides(args)
        assert any("resume_mode=auto" in o for o in overrides)

    def test_resume_mode_for_iteration_nonzero(self, mock_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.run_verl import build_verl_overrides

        ckpt_dir = tmp_path / "ckpt" / "latest"
        ckpt_dir.mkdir(parents=True)

        import argparse
        args = argparse.Namespace(
            prompts=str(mock_prompts), task_dir=".", checkpoint_dir=str(tmp_path / "ckpt"),
            output_dir=str(tmp_path), model_path="m", iteration=3,
            rollouts_per_prompt=64, num_gpus=1, rollout_tp=1, lora_rank=32,
            learning_rate=4e-5, kl_coef=0.1, temperature=0.8,
            phase1_max_tokens=26000, eval_timeout=60,
            parquet_path=str(tmp_path / "p.parquet"),
        )
        overrides = build_verl_overrides(args)
        assert any("resume_mode=resume_path" in o for o in overrides)


class TestPostProcessResults:
    def test_writes_evaluation_results(
        self, mock_rollout_log: Path, mock_prompts: Path, tmp_path: Path,
    ) -> None:
        from factory.lumen.run_verl import post_process_results

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with open(mock_prompts) as f:
            prompts_data = json.load(f)

        results = post_process_results(mock_rollout_log, output_dir, prompts_data, iteration=0)
        assert results["num_rollouts"] == 512
        assert results["best_score"] > 0
        assert len(results["per_prompt_stats"]) == 8
        assert (output_dir / "evaluation_results.json").exists()
        assert (output_dir / "rollouts.jsonl").exists()

    def test_rollouts_jsonl_has_correct_count(
        self, mock_rollout_log: Path, mock_prompts: Path, tmp_path: Path,
    ) -> None:
        from factory.lumen.run_verl import post_process_results

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with open(mock_prompts) as f:
            prompts_data = json.load(f)

        post_process_results(mock_rollout_log, output_dir, prompts_data, iteration=0)
        with open(output_dir / "rollouts.jsonl") as f:
            lines = f.readlines()
        assert len(lines) == 512
