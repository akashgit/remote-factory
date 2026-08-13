"""VERL reward function for Einstein Arena evaluation.

Evaluates model output by extracting code, executing it in a sandbox,
and running the task's test.sh verifier to produce a score.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def extract_last_code_block(text: str) -> str | None:
    """Extract the last fenced code block from model output."""
    pattern = r"```(?:python)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip()


def evaluate_code_solution(code: str, task_dir: Path, timeout: int = 60) -> float:
    """Execute code in a sandbox and evaluate with test.sh.

    1. Write code to a temp file
    2. Execute it (produces solution.json in workspace)
    3. Run test.sh verifier
    4. Read score.txt
    """
    test_sh = (task_dir / "tests" / "test.sh").resolve()
    if not test_sh.exists():
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

        try:
            env = os.environ.copy()
            env["WORKSPACE"] = str(workspace)
            subprocess.run(
                ["bash", str(test_sh)],
                cwd=str(tmpdir),
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, Exception):
            return 0.0

        score_file = workspace / "score.txt"
        if score_file.exists():
            try:
                return float(score_file.read_text().strip())
            except ValueError:
                return 0.0
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
        score = evaluate_code_solution(code, task_dir, timeout=eval_timeout)
    except Exception as e:
        return {"score": 0.0, "code": code, "eval_msg": f"eval error: {e}"}

    return {
        "score": score,
        "code": code,
        "eval_msg": "" if score > 0 else "score is zero",
    }
