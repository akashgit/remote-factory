"""Tests for Lumen VERL reward function (Einstein Arena evaluation)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest


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

        code = (
            'import json\n'
            'solution = {"value": 42}\n'
            'with open("solution.json", "w") as f:\n'
            '    json.dump(solution, f)\n'
        )
        score = evaluate_code_solution(code, mock_task_dir, timeout=10)
        assert score == 1.5

    def test_code_that_produces_no_solution(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = "x = 1 + 1"
        score = evaluate_code_solution(code, mock_task_dir, timeout=10)
        assert score == 0.0

    def test_timeout_returns_zero(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = "import time; time.sleep(100)"
        score = evaluate_code_solution(code, mock_task_dir, timeout=1)
        assert score == 0.0


class TestComputeScore:
    def test_verl_interface(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import compute_score

        solution_str = (
            '<think>thinking</think>\n'
            '```python\n'
            'import json\n'
            'with open("solution.json", "w") as f:\n'
            '    json.dump({"value": 1}, f)\n'
            '```'
        )
        result = compute_score(
            data_source="lumen",
            solution_str=solution_str,
            ground_truth=None,
            extra_info={"task_dir": str(mock_task_dir), "eval_timeout": 10},
        )
        assert isinstance(result, dict)
        assert result["score"] == 1.5
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
