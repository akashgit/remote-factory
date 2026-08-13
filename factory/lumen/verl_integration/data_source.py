"""Convert Lumen prompts.json to VERL-compatible parquet format."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def create_parquet_from_prompts(
    prompts_json_path: Path,
    output_path: Path,
    task_dir: str = "",
    data_source: str = "lumen",
    eval_timeout: int = 60,
) -> Path:
    """Read prompts.json and write a VERL-compatible parquet file.

    The parquet matches VERL's expected schema (same as Discover's training data):
      - prompt: numpy array of chat message dicts [{"role": "user", "content": "..."}]
      - data_source: str identifier for the reward function
      - ability: str ("code")
      - reward_model: dict ({"style": "rule", "ground_truth": ""})
      - extra_info: dict with task_dir, prompt_idx, strategy, eval_timeout
    """
    with open(prompts_json_path) as f:
        prompts_data = json.load(f)

    rows = []
    for p in prompts_data["prompts"]:
        chat_messages = np.array(
            [{"role": "user", "content": p["prompt_text"]}],
            dtype=object,
        )
        rows.append({
            "prompt": chat_messages,
            "data_source": data_source,
            "ability": "code",
            "reward_model": {"style": "rule", "ground_truth": ""},
            "extra_info": {
                "split": "train",
                "index": p["prompt_idx"],
                "prompt_idx": p["prompt_idx"],
                "strategy": p["strategy"],
                "task_dir": task_dir,
                "eval_timeout": eval_timeout,
                "scoring_direction": prompts_data.get("scoring_direction", "maximize"),
            },
        })

    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path)
    return output_path
