"""Tests for factory/templates/score.py — template eval script."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from factory.templates.score import eval_lint, eval_tests, main


# ---------------------------------------------------------------------------
# eval_tests
# ---------------------------------------------------------------------------


class TestEvalTests:
    """Tests for eval_tests()."""

    @patch("factory.templates.score.subprocess.run")
    def test_passing_tests(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 0.3s",
            stderr="",
        )
        result = eval_tests()
        assert result["name"] == "tests"
        assert result["score"] == 1.0
        assert result["weight"] == 0.5
        assert result["passed"] is True
        assert "5 passed" in result["details"]

    @patch("factory.templates.score.subprocess.run")
    def test_failing_tests(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="2 failed, 3 passed",
            stderr="",
        )
        result = eval_tests()
        assert result["name"] == "tests"
        assert result["score"] == 0.0
        assert result["weight"] == 0.5
        assert result["passed"] is False
        assert "2 failed" in result["details"]

    @patch("factory.templates.score.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=300)
        result = eval_tests()
        assert result["name"] == "tests"
        assert result["score"] == 0.0
        assert result["passed"] is False
        assert "timed out" in result["details"]

    @patch("factory.templates.score.subprocess.run")
    def test_empty_stdout_falls_back_to_stderr(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="FATAL: collection error",
        )
        result = eval_tests()
        assert result["passed"] is False
        assert "FATAL" in result["details"]

    @patch("factory.templates.score.subprocess.run")
    def test_details_truncated_to_500_chars(self, mock_run: MagicMock) -> None:
        long_output = "x" * 1000
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=long_output,
            stderr="",
        )
        result = eval_tests()
        assert len(result["details"]) == 500


# ---------------------------------------------------------------------------
# eval_lint
# ---------------------------------------------------------------------------


class TestEvalLint:
    """Tests for eval_lint()."""

    @patch("factory.templates.score.subprocess.run")
    def test_clean_lint(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All checks passed!",
            stderr="",
        )
        result = eval_lint()
        assert result["name"] == "lint"
        assert result["score"] == 1.0
        assert result["weight"] == 0.3
        assert result["passed"] is True

    @patch("factory.templates.score.subprocess.run")
    def test_lint_violations_partial_score(self, mock_run: MagicMock) -> None:
        # 3 violation lines + 1 summary line = 4 lines total
        # violation_count = max(0, 4 - 1) = 3
        # score = max(0.0, 1.0 - 3 * 0.1) = 0.7
        stdout = (
            "file1.py:1:1: E501 line too long\n"
            "file2.py:2:1: E302 expected 2 blank lines\n"
            "file3.py:3:1: W291 trailing whitespace\n"
            "Found 3 errors."
        )
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=stdout,
            stderr="",
        )
        result = eval_lint()
        assert result["name"] == "lint"
        assert result["passed"] is False
        assert result["score"] == 0.7

    @patch("factory.templates.score.subprocess.run")
    def test_lint_many_violations_score_floors_at_zero(self, mock_run: MagicMock) -> None:
        # 20 violation lines + 1 summary = 21 lines, violation_count = 20
        # score = max(0.0, 1.0 - 20 * 0.1) = max(0.0, -1.0) = 0.0
        lines = [f"file.py:{i}:1: E501 line too long" for i in range(20)]
        lines.append("Found 20 errors.")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="\n".join(lines),
            stderr="",
        )
        result = eval_lint()
        assert result["score"] == 0.0

    @patch("factory.templates.score.subprocess.run")
    def test_lint_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ruff", timeout=60)
        result = eval_lint()
        assert result["name"] == "lint"
        assert result["score"] == 0.0
        assert result["weight"] == 0.3
        assert result["passed"] is False
        assert "timed out" in result["details"]

    @patch("factory.templates.score.subprocess.run")
    def test_lint_empty_stdout(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        result = eval_lint()
        assert result["details"] == "No output"

    @patch("factory.templates.score.subprocess.run")
    def test_lint_details_truncated(self, mock_run: MagicMock) -> None:
        long_output = "v" * 1000
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=long_output,
            stderr="",
        )
        result = eval_lint()
        assert len(result["details"]) == 500


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for main() — JSON output to stdout."""

    @patch("factory.templates.score.EVALS", [])
    def test_main_empty_evals(self, capsys) -> None:
        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == {"results": []}

    @patch("factory.templates.score.subprocess.run")
    def test_main_outputs_valid_json(self, mock_run: MagicMock, capsys) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "results" in data
        assert len(data["results"]) == 2
        names = {r["name"] for r in data["results"]}
        assert names == {"tests", "lint"}

    @patch("factory.templates.score.EVALS")
    def test_main_calls_all_evals(self, mock_evals: MagicMock, capsys) -> None:
        fn1 = MagicMock(return_value={"name": "a", "score": 1.0, "weight": 0.5, "passed": True, "details": ""})
        fn2 = MagicMock(return_value={"name": "b", "score": 0.5, "weight": 0.5, "passed": False, "details": "err"})
        mock_evals.__iter__ = MagicMock(return_value=iter([fn1, fn2]))
        main()
        fn1.assert_called_once()
        fn2.assert_called_once()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data["results"]) == 2
        assert data["results"][0]["name"] == "a"
        assert data["results"][1]["name"] == "b"

    @patch("factory.templates.score.subprocess.run")
    def test_main_trailing_newline(self, mock_run: MagicMock, capsys) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        main()
        captured = capsys.readouterr()
        assert captured.out.endswith("\n")
