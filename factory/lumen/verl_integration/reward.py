"""VERL reward function for Einstein Arena evaluation.

Evaluates model output by extracting code, executing it in a sandbox,
and running the task's verifier to produce a score.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_last_code_block(text: str) -> str | None:
    """Extract the last fenced code block from model output."""
    pattern = r"```(?:[Pp]ython|[Pp]y)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip()


def evaluate_code_solution(code: str, task_dir: Path, timeout: int = 60) -> float:
    """Execute code in a sandbox and evaluate with the task's verifier.

    1. Write code to a temp file
    2. Execute it (produces solution.json in workspace)
    3. Load verifier.py and score the solution
    """
    verifier_path = (task_dir / "verifier.py").resolve()
    if not verifier_path.exists():
        return 0.0

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()

        code_file = Path(tmpdir) / "run.py"
        code_file.write_text(code)

        try:
            subprocess.run(
                ["python3", str(code_file)],
                cwd=str(workspace),
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 0.0
        except Exception:
            return 0.0

        solution_file = workspace / "solution.json"
        if not solution_file.exists():
            return 0.0

        try:
            import json
            with open(solution_file) as f:
                data = json.load(f)

            spec = importlib.util.spec_from_file_location("verifier", verifier_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["verifier"] = module
            spec.loader.exec_module(module)

            return float(module.evaluate(data))
        except Exception:
            return 0.0


def compute_score(
    data_source: str,
    solution_str: str,
    ground_truth: dict | None,
    extra_info: dict,
) -> dict:
    """VERL reward interface.

    Called by VERL's reward pipeline for each completion. Extracts code from
    the model's response, executes it, and evaluates via Einstein Arena verifier.
    """
    code = extract_last_code_block(solution_str)
    if not code:
        return {"score": 0.0, "code": "", "eval_msg": "no code block extracted"}

    task_dir = Path(extra_info.get("task_dir", ""))
    eval_timeout = extra_info.get("eval_timeout", 60)

    try:
        raw_score = evaluate_code_solution(code, task_dir, timeout=eval_timeout)
    except Exception as e:
        return {"score": 0.0, "raw_score": 0.0, "code": code, "eval_msg": f"eval error: {e}"}

    from factory.lumen.reward import shape_reward

    direction = extra_info.get("scoring_direction", "maximize")
    reward_cfg = extra_info.get("reward", None)
    score = shape_reward(raw_score, direction, reward_cfg)

    return {
        "score": score,
        "raw_score": raw_score,
        "code": code,
        "eval_msg": "" if raw_score > 0 else "score is zero",
    }
