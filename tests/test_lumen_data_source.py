"""Tests for Lumen VERL data source (prompts.json → parquet)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def sample_prompts(tmp_path: Path) -> Path:
    """Create a minimal prompts.json for testing."""
    data = {
        "iteration": 0,
        "problem_type": "geometry",
        "scoring_direction": "maximize",
        "solution_schema": {"circles": "array of [x, y, r]"},
        "prompts": [
            {
                "prompt_idx": i,
                "strategy": f"strategy_{i}",
                "prompt_text": f"Optimize using strategy {i}. Output solution.json.",
            }
            for i in range(8)
        ],
    }
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(data))
    return path


class TestCreateParquetFromPrompts:
    def test_creates_parquet_file(self, sample_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        result = create_parquet_from_prompts(sample_prompts, output)
        assert result == output
        assert output.exists()

    def test_parquet_has_correct_row_count(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output)
        df = pd.read_parquet(output)
        assert len(df) == 8

    def test_parquet_has_required_columns(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output)
        df = pd.read_parquet(output)
        required = {"prompt", "data_source", "ability", "reward_model", "extra_info"}
        assert required.issubset(set(df.columns))

    def test_prompt_column_is_chat_messages(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output)
        df = pd.read_parquet(output)
        row0_prompt = df.iloc[0]["prompt"]
        assert len(row0_prompt) == 1
        assert row0_prompt[0]["role"] == "user"
        assert "strategy 0" in row0_prompt[0]["content"]

    def test_extra_info_contains_task_metadata(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output, task_dir="/path/to/task")
        df = pd.read_parquet(output)
        info = df.iloc[0]["extra_info"]
        assert info["task_dir"] == "/path/to/task"
        assert info["prompt_idx"] == 0
        assert info["strategy"] == "strategy_0"
