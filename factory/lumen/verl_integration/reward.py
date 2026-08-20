"""VERL reward function for Einstein Arena evaluation.

Evaluates model output by extracting code, executing it in a sandbox,
and running the task's verifier to produce a score.

Follows the Discover pattern: the model defines a `run()` function that
returns the solution dict. We call it via subprocess + pickle, then pass
the result to verifier.evaluate(data).
"""

from __future__ import annotations

import importlib.util
import os
import pickle
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_last_code_block(text: str) -> str | None:
    """Extract the last fenced code block from model output.

    Language-agnostic: matches ```python, ```py, ```cpp, bare ```, etc.
    """
    pattern = r"```\w*\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip()


_RUNNER_TEMPLATE = """\
import sys
import pickle
import importlib.util

spec = importlib.util.spec_from_file_location("solution", {code_path!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

result = module.run()

with open({results_path!r}, "wb") as f:
    pickle.dump(result, f)
"""


def evaluate_code_solution(code: str, task_dir: Path, timeout: int = 60) -> tuple[float, dict]:
    """Execute code's run() function and evaluate with the task's verifier.

    The code must define a `run()` function that returns the solution dict.
    A runner script imports the code, calls run(), and pickles the return
    value. The result is then scored by verifier.py's evaluate(data).

    Returns:
        (score, solution) tuple. solution is {} if execution failed.
    """
    verifier_path = (task_dir / "verifier.py").resolve()
    if not verifier_path.exists():
        return 0.0, {}

    with tempfile.TemporaryDirectory() as tmpdir:
        code_file = Path(tmpdir) / "solution.py"
        code_file.write_text(code)

        results_file = Path(tmpdir) / "results.pkl"

        runner_code = _RUNNER_TEMPLATE.format(
            code_path=str(code_file), results_path=str(results_file),
        )
        runner_file = Path(tmpdir) / "runner.py"
        runner_file.write_text(runner_code)

        try:
            subprocess.run(
                ["python3", str(runner_file)],
                cwd=tmpdir,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 0.0, {}
        except Exception:
            return 0.0, {}

        if not results_file.exists():
            return 0.0, {}

        try:
            with open(results_file, "rb") as f:
                data = pickle.load(f)

            if not isinstance(data, dict):
                data = {"result": data}

            spec = importlib.util.spec_from_file_location("verifier", verifier_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["verifier"] = module
            spec.loader.exec_module(module)

            score = float(module.evaluate(data))
            return score, data
        except Exception:
            return 0.0, {}


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
        return {"score": 0.0, "code": "", "eval_msg": "no code block extracted", "solution": {}}

    task_dir = Path(extra_info.get("task_dir", ""))
    eval_timeout = extra_info.get("eval_timeout", 60)

    try:
        raw_score, solution = evaluate_code_solution(code, task_dir, timeout=eval_timeout)
    except Exception as e:
        return {"score": 0.0, "raw_score": 0.0, "code": code, "eval_msg": f"eval error: {e}", "solution": {}}

    from factory.lumen.reward import shape_reward

    direction = extra_info.get("scoring_direction", "maximize")
    reward_cfg = extra_info.get("reward", None)
    score = shape_reward(raw_score, direction, reward_cfg)

    return {
        "score": score,
        "raw_score": raw_score,
        "code": code,
        "eval_msg": "" if raw_score > 0 else "score is zero",
        "solution": solution,
    }
