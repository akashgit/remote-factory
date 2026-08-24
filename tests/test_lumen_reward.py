"""Tests for Lumen reward shaping and VERL reward function."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from factory.lumen.reward import shape_reward


# ── Reward shaping tests ───────────────────────────────────────


class TestShapeRewardIdentity:
    def test_no_config_returns_raw(self) -> None:
        assert shape_reward(0.75, "maximize") == 0.75

    def test_explicit_identity(self) -> None:
        assert shape_reward(0.75, "maximize", {"type": "identity"}) == 0.75

    def test_non_finite_returns_negative_one(self) -> None:
        assert shape_reward(float("-inf"), "maximize") == -1.0
        assert shape_reward(float("nan"), "minimize") == -1.0


class TestShapeRewardLinear:
    def test_maximize_above_baseline(self) -> None:
        cfg = {"type": "linear", "baseline": 2.0, "scale": 0.5, "clip_min": -1.0, "clip_max": 1.0}
        assert shape_reward(3.0, "maximize", cfg) == 0.5

    def test_minimize_flips_sign(self) -> None:
        cfg = {"type": "linear", "baseline": 100.0, "scale": 0.01, "clip_min": -1.0, "clip_max": 1.0}
        assert abs(shape_reward(80.0, "minimize", cfg) - 0.2) < 1e-9

    def test_clips_to_range(self) -> None:
        cfg = {"type": "linear", "baseline": 0.0, "scale": 10.0, "clip_min": -1.0, "clip_max": 1.0}
        assert shape_reward(5.0, "maximize", cfg) == 1.0
        assert shape_reward(-5.0, "maximize", cfg) == -1.0


class TestShapeRewardBinary:
    def test_maximize_above_threshold(self) -> None:
        assert shape_reward(3.0, "maximize", {"type": "binary", "threshold": 2.5}) == 1.0

    def test_maximize_below_threshold(self) -> None:
        assert shape_reward(2.0, "maximize", {"type": "binary", "threshold": 2.5}) == 0.0

    def test_minimize_below_threshold(self) -> None:
        assert shape_reward(80.0, "minimize", {"type": "binary", "threshold": 100.0}) == 1.0


class TestShapeRewardRelative:
    def test_maximize_improvement(self) -> None:
        cfg = {"type": "relative", "baseline": 2.0, "clip_min": -1.0, "clip_max": 1.0}
        assert abs(shape_reward(2.4, "maximize", cfg) - 0.2) < 1e-9

    def test_minimize_improvement(self) -> None:
        cfg = {"type": "relative", "baseline": 100.0, "clip_min": -1.0, "clip_max": 1.0}
        assert abs(shape_reward(90.0, "minimize", cfg) - 0.1) < 1e-9

    def test_zero_baseline_returns_zero(self) -> None:
        assert shape_reward(5.0, "maximize", {"type": "relative", "baseline": 0.0}) == 0.0


class TestShapeRewardReciprocal:
    def test_default_scale(self) -> None:
        cfg = {"type": "reciprocal"}
        assert abs(shape_reward(1.0, "minimize", cfg) - 1.0) < 1e-6

    def test_custom_scale(self) -> None:
        cfg = {"type": "reciprocal", "scale": 1500.0, "epsilon": 0.0}
        assert shape_reward(3.0, "minimize", cfg) == 500.0

    def test_epsilon_prevents_division_by_zero(self) -> None:
        cfg = {"type": "reciprocal", "epsilon": 1e-8}
        result = shape_reward(0.0, "minimize", cfg)
        assert result == 1e8

    def test_negative_raw_returns_zero(self) -> None:
        cfg = {"type": "reciprocal", "scale": 1.0}
        assert shape_reward(-5.0, "minimize", cfg) == 0.0

    def test_direction_ignored(self) -> None:
        cfg = {"type": "reciprocal", "scale": 100.0, "epsilon": 0.0}
        assert shape_reward(10.0, "maximize", cfg) == shape_reward(10.0, "minimize", cfg)


class TestShapeRewardUnknown:
    def test_unknown_type_returns_raw(self) -> None:
        assert shape_reward(0.75, "maximize", {"type": "unknown"}) == 0.75


# ── VERL integration tests ─────────────────────────────────────


@pytest.fixture()
def mock_task_dir(tmp_path: Path) -> Path:
    """Create a minimal Einstein Arena task directory with a verifier.py."""
    task_dir = tmp_path / "test-task"
    task_dir.mkdir(parents=True)

    verifier = task_dir / "verifier.py"
    verifier.write_text(
        'def evaluate(data):\n'
        '    return 1.5\n'
    )

    instruction = task_dir / "instruction.md"
    instruction.write_text("# Test Task\nScoring Direction: MAXIMIZE\n")
    return task_dir


class TestExtractLastCodeBlock:
    def test_extracts_python_block(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        text = 'Some thinking\n```python\nprint("hello")\n```\nMore text'
        assert extract_last_code_block(text) == 'print("hello")'

    def test_returns_last_block_when_multiple(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        text = '```python\nfirst()\n```\ntext\n```python\nsecond()\n```'
        assert extract_last_code_block(text) == "second()"

    def test_returns_none_when_no_code(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        assert extract_last_code_block("no code here") is None

    def test_handles_generic_code_fence(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        text = '```\nimport json\n```'
        assert extract_last_code_block(text) == "import json"


class TestEvaluateCodeSolution:
    def test_valid_code_gets_score(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = 'def run():\n    return {"value": 42}\n'
        score, solution = evaluate_code_solution(code, mock_task_dir, timeout=10)
        assert score == 1.5
        assert isinstance(solution, dict)

    def test_code_that_produces_no_solution(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = "x = 1 + 1"
        score, solution = evaluate_code_solution(code, mock_task_dir, timeout=10)
        assert score == 0.0

    def test_timeout_returns_zero(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = "import time; time.sleep(100)"
        score, solution = evaluate_code_solution(code, mock_task_dir, timeout=1)
        assert score == 0.0


class TestComputeScore:
    def test_verl_interface(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import compute_score

        solution_str = (
            '<think>thinking</think>\n'
            '```python\n'
            'def run():\n'
            '    return {"value": 1}\n'
            '```'
        )
        result = compute_score(
            data_source="lumen",
            solution_str=solution_str,
            ground_truth=None,
            extra_info={"task_dir": str(mock_task_dir), "eval_timeout": 10},
        )
        assert isinstance(result, dict)
        assert result["raw_score"] == 1.5
        assert result["score"] == 1.5  # no reward config → identity
        assert "code" in result

    def test_no_code_block_returns_zero(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import compute_score

        result = compute_score(
            data_source="lumen",
            solution_str="no code here",
            ground_truth=None,
            extra_info={"task_dir": str(mock_task_dir), "eval_timeout": 10},
        )
        assert result["score"] == 0.0
        assert "no code block" in result["eval_msg"]
