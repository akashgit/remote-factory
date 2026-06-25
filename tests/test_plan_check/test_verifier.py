"""Unit tests for factory.plan_check.verifier — subprocess calls are mocked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.plan_check.models import AcceptanceCriterion
from factory.plan_check.parser import ParsedHypothesis
from factory.plan_check.verifier import (
    detect_stubs,
    verify_criteria,
    verify_hypothesis,
    verify_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _criterion(
    method: str,
    target: dict | None = None,
    *,
    criterion_id: str = "H1.test",
    hypothesis_id: str = "H1",
    criterion_type: str = "deliverable",
    description: str = "test criterion",
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        criterion_id=criterion_id,
        hypothesis_id=hypothesis_id,
        criterion_type=criterion_type,
        description=description,
        verification_method=method,
        target=target or {},
    )


def _hypothesis(h_id: str = "H1", title: str = "Test hypothesis") -> ParsedHypothesis:
    return ParsedHypothesis(id=h_id, title=title)


# ---------------------------------------------------------------------------
# file_exists
# ---------------------------------------------------------------------------


def test_verify_file_exists_pass(tmp_path: Path) -> None:
    (tmp_path / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    c = _criterion("file_exists", {"path": "src/main.py"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is True
    assert result.actual_value == "exists"


def test_verify_file_exists_fail(tmp_path: Path) -> None:
    c = _criterion("file_exists", {"path": "nonexistent.py"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.actual_value == "not found"


# ---------------------------------------------------------------------------
# function_exists
# ---------------------------------------------------------------------------


def test_verify_function_exists_pass(tmp_path: Path) -> None:
    py = tmp_path / "lib.py"
    py.write_text("def analyze_completion(data):\n    return len(data)\n")
    c = _criterion("function_exists", {"path": "lib.py", "symbol": "analyze_completion"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is True


def test_verify_function_exists_fail(tmp_path: Path) -> None:
    py = tmp_path / "lib.py"
    py.write_text("def other_func():\n    pass\n")
    c = _criterion("function_exists", {"path": "lib.py", "symbol": "analyze_completion"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert "not found" in (result.actual_value or "")


def test_verify_function_exists_stub(tmp_path: Path) -> None:
    py = tmp_path / "lib.py"
    py.write_text("def analyze_completion():\n    pass\n")
    c = _criterion("function_exists", {"path": "lib.py", "symbol": "analyze_completion"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert "stub" in (result.actual_value or "")


def test_verify_function_exists_stub_ellipsis(tmp_path: Path) -> None:
    py = tmp_path / "lib.py"
    py.write_text("def analyze_completion():\n    ...\n")
    c = _criterion("function_exists", {"path": "lib.py", "symbol": "analyze_completion"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert "stub" in (result.actual_value or "")


def test_verify_function_exists_stub_not_implemented(tmp_path: Path) -> None:
    py = tmp_path / "lib.py"
    py.write_text("def analyze_completion():\n    raise NotImplementedError\n")
    c = _criterion("function_exists", {"path": "lib.py", "symbol": "analyze_completion"})
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert "stub" in (result.actual_value or "")


# ---------------------------------------------------------------------------
# test_passes
# ---------------------------------------------------------------------------


def test_verify_test_passes_pass(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "1 passed"
    mock_result.stderr = ""
    c = _criterion(
        "test_passes",
        {"test_name": "test_something"},
        criterion_type="test_requirement",
    )
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is True
    assert result.actual_value == "passed"


def test_verify_test_passes_fail(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "FAILED test_something - AssertionError"
    mock_result.stderr = ""
    c = _criterion(
        "test_passes",
        {"test_name": "test_something"},
        criterion_type="test_requirement",
    )
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.actual_value == "failed"


def test_verify_test_passes_not_found(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 5
    mock_result.stdout = "no tests ran"
    mock_result.stderr = ""
    c = _criterion(
        "test_passes",
        {"test_name": "test_nonexistent"},
        criterion_type="test_requirement",
    )
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.actual_value == "test not found"


# ---------------------------------------------------------------------------
# eval_score
# ---------------------------------------------------------------------------


def test_verify_eval_score_pass(tmp_path: Path) -> None:
    scores_dir = tmp_path / ".factory" / "eval"
    scores_dir.mkdir(parents=True)
    (scores_dir / "scores.json").write_text(json.dumps({"tests": 0.75, "lint": 0.9}))
    c = _criterion(
        "eval_score",
        {"dimension": "tests", "min_expected": 0.7},
        criterion_type="eval_target",
    )
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is True
    assert result.actual_value == "tests=0.75"
    assert result.expected_value == "tests>=0.7"


def test_verify_eval_score_fail(tmp_path: Path) -> None:
    scores_dir = tmp_path / ".factory" / "eval"
    scores_dir.mkdir(parents=True)
    (scores_dir / "scores.json").write_text(json.dumps({"tests": 0.55}))
    c = _criterion(
        "eval_score",
        {"dimension": "tests", "min_expected": 0.7},
        criterion_type="eval_target",
    )
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.actual_value == "tests=0.55"
    assert result.expected_value == "tests>=0.7"


def test_verify_eval_score_dimension_missing(tmp_path: Path) -> None:
    scores_dir = tmp_path / ".factory" / "eval"
    scores_dir.mkdir(parents=True)
    (scores_dir / "scores.json").write_text(json.dumps({"lint": 0.9}))
    c = _criterion(
        "eval_score",
        {"dimension": "tests", "min_expected": 0.7},
        criterion_type="eval_target",
    )
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert "not found" in (result.actual_value or "")


# ---------------------------------------------------------------------------
# command_exits_zero
# ---------------------------------------------------------------------------


def test_verify_command_exits_zero_pass(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "ok"
    mock_result.stderr = ""
    c = _criterion(
        "command_exits_zero",
        {"command": "echo ok"},
        criterion_type="functional",
    )
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is True


def test_verify_command_exits_zero_fail(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "error"
    c = _criterion(
        "command_exits_zero",
        {"command": "false"},
        criterion_type="functional",
    )
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.actual_value == "exit code 1"


# ---------------------------------------------------------------------------
# grep_match
# ---------------------------------------------------------------------------


def test_verify_grep_match_pass(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "./lib.py:def hello():\n"
    mock_result.stderr = ""
    c = _criterion("grep_match", {"pattern": "def hello"})
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is True
    assert "1 matches" in (result.actual_value or "")


def test_verify_grep_match_fail(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = ""
    c = _criterion("grep_match", {"pattern": "nonexistent_symbol"})
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is False


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_verify_timeout(tmp_path: Path) -> None:
    c = _criterion(
        "test_passes",
        {"test_name": "test_slow"},
        criterion_type="test_requirement",
    )
    with patch(
        "factory.plan_check.verifier.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=60),
    ):
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.error is not None
    assert "timed out" in result.error


# ---------------------------------------------------------------------------
# detect_stubs
# ---------------------------------------------------------------------------


def test_detect_stubs_pass_body() -> None:
    source = "def foo():\n    pass\n"
    assert len(detect_stubs(source, "foo")) > 0


def test_detect_stubs_ellipsis_body() -> None:
    source = "def foo():\n    ...\n"
    assert len(detect_stubs(source, "foo")) > 0


def test_detect_stubs_not_implemented() -> None:
    source = "def foo():\n    raise NotImplementedError\n"
    assert len(detect_stubs(source, "foo")) > 0


def test_detect_stubs_real_body() -> None:
    source = "def foo():\n    return 42\n"
    assert detect_stubs(source, "foo") == []


def test_detect_stubs_with_docstring_and_pass() -> None:
    source = 'def foo():\n    """Docstring."""\n    pass\n'
    assert len(detect_stubs(source, "foo")) > 0


# ---------------------------------------------------------------------------
# eval_score — delta fails without baseline
# ---------------------------------------------------------------------------


def test_verify_eval_score_delta_fails_without_baseline(tmp_path: Path) -> None:
    scores_dir = tmp_path / ".factory" / "eval"
    scores_dir.mkdir(parents=True)
    (scores_dir / "scores.json").write_text(json.dumps({"tests": 0.75}))
    c = _criterion(
        "eval_score",
        {"dimension": "tests", "delta": 0.1},
        criterion_type="eval_target",
    )
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert "baseline" in (result.actual_value or "").lower()


# ---------------------------------------------------------------------------
# command_exits_zero — uses shlex.split (no shell=True)
# ---------------------------------------------------------------------------


def test_verify_command_shlex_split(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "ok"
    mock_result.stderr = ""
    c = _criterion(
        "command_exits_zero",
        {"command": "echo hello world"},
        criterion_type="functional",
    )
    with patch("factory.plan_check.verifier.subprocess.run", return_value=mock_result) as mock_run:
        [result] = verify_criteria([c], tmp_path)
    assert result.passed is True
    call_args = mock_run.call_args
    assert call_args[0][0] == ["echo", "hello", "world"]
    assert call_args[1].get("shell") is None or call_args[1].get("shell") is False


def test_verify_command_invalid_shlex(tmp_path: Path) -> None:
    c = _criterion(
        "command_exits_zero",
        {"command": "echo 'unterminated"},
        criterion_type="functional",
    )
    [result] = verify_criteria([c], tmp_path)
    assert result.passed is False
    assert result.error is not None
    assert "Invalid command syntax" in result.error


# ---------------------------------------------------------------------------
# verify_hypothesis
# ---------------------------------------------------------------------------


def test_verify_hypothesis_all_pass(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "c.txt").write_text("hello\n")

    criteria = [
        _criterion("file_exists", {"path": "a.py"}, criterion_id="H1.1"),
        _criterion("file_exists", {"path": "b.py"}, criterion_id="H1.2"),
        _criterion("file_exists", {"path": "c.txt"}, criterion_id="H1.3"),
    ]
    h = _hypothesis()
    verdict = verify_hypothesis(h, criteria, tmp_path)
    assert verdict.passed is True
    assert verdict.pass_rate == 1.0
    assert verdict.unsatisfied == []


def test_verify_hypothesis_partial_fail(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")

    criteria = [
        _criterion("file_exists", {"path": "a.py"}, criterion_id="H1.1", description="a.py exists"),
        _criterion("file_exists", {"path": "b.py"}, criterion_id="H1.2", description="b.py exists"),
        _criterion(
            "file_exists",
            {"path": "missing.py"},
            criterion_id="H1.3",
            description="missing.py exists",
        ),
    ]
    h = _hypothesis()
    verdict = verify_hypothesis(h, criteria, tmp_path)
    assert verdict.passed is False
    assert len(verdict.unsatisfied) == 1
    assert "missing.py exists" in verdict.unsatisfied


# ---------------------------------------------------------------------------
# verify_plan
# ---------------------------------------------------------------------------


def test_verify_plan_summary(tmp_path: Path) -> None:
    (tmp_path / "exists.py").write_text("x = 1\n")

    h1 = _hypothesis("H1", "First")
    c1 = [_criterion("file_exists", {"path": "exists.py"}, criterion_id="H1.1", hypothesis_id="H1")]

    h2 = _hypothesis("H2", "Second")
    c2 = [
        _criterion("file_exists", {"path": "exists.py"}, criterion_id="H2.1", hypothesis_id="H2"),
        _criterion(
            "file_exists", {"path": "gone.py"}, criterion_id="H2.2", hypothesis_id="H2"
        ),
    ]

    report = verify_plan([(h1, c1), (h2, c2)], tmp_path)
    assert report.summary.total_criteria == 3
    assert report.summary.passed_criteria == 2
    assert report.summary.failed_criteria == 1
    assert report.summary.all_passed is False
    assert "H2" in report.summary.failed_hypotheses
    assert "H1" not in report.summary.failed_hypotheses
