"""Integration tests — real files, no mocking."""

from __future__ import annotations

from pathlib import Path

from factory.plan_check.models import AcceptanceCriterion
from factory.plan_check.verifier import verify_criteria


def _criterion(
    method: str,
    target: dict,
    *,
    criterion_id: str = "H1.test",
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        criterion_id=criterion_id,
        hypothesis_id="H1",
        criterion_type="deliverable",
        description="integration test criterion",
        verification_method=method,
        target=target,
    )


def test_e2e_verify_real_file(tmp_path: Path) -> None:
    """Create a real temp file and verify file_exists without mocking."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    real_file = project / "src" / "main.py"
    real_file.write_text("print('hello world')\n")

    c_pass = _criterion("file_exists", {"path": "src/main.py"}, criterion_id="H1.1")
    c_fail = _criterion("file_exists", {"path": "src/missing.py"}, criterion_id="H1.2")

    results = verify_criteria([c_pass, c_fail], project)
    assert results[0].passed is True
    assert results[1].passed is False


def test_e2e_verify_real_function(tmp_path: Path) -> None:
    """Create a temp .py with a real function and verify function_exists without mocking."""
    project = tmp_path / "project"
    project.mkdir()
    py_file = project / "module.py"
    py_file.write_text(
        "def analyze_completion(data: list) -> int:\n"
        "    return len(data)\n"
        "\n"
        "class MyModel:\n"
        "    def predict(self):\n"
        "        return 0\n"
    )

    c_func = _criterion(
        "function_exists",
        {"path": "module.py", "symbol": "analyze_completion"},
        criterion_id="H1.func",
    )
    c_class = _criterion(
        "function_exists",
        {"path": "module.py", "symbol": "MyModel"},
        criterion_id="H1.class",
    )
    c_missing = _criterion(
        "function_exists",
        {"path": "module.py", "symbol": "nonexistent"},
        criterion_id="H1.missing",
    )

    results = verify_criteria([c_func, c_class, c_missing], project)
    assert results[0].passed is True
    assert results[1].passed is True
    assert results[2].passed is False
    assert "not found" in (results[2].actual_value or "")


def test_e2e_verify_stub_detection(tmp_path: Path) -> None:
    """Real stub detection without mocking."""
    project = tmp_path / "project"
    project.mkdir()
    py_file = project / "stubs.py"
    py_file.write_text(
        "def real_func():\n"
        "    return 42\n"
        "\n"
        "def stub_pass():\n"
        "    pass\n"
        "\n"
        "def stub_ellipsis():\n"
        "    ...\n"
        "\n"
        "def stub_not_impl():\n"
        "    raise NotImplementedError\n"
    )

    criteria = [
        _criterion("function_exists", {"path": "stubs.py", "symbol": "real_func"}, criterion_id="1"),
        _criterion("function_exists", {"path": "stubs.py", "symbol": "stub_pass"}, criterion_id="2"),
        _criterion(
            "function_exists", {"path": "stubs.py", "symbol": "stub_ellipsis"}, criterion_id="3"
        ),
        _criterion(
            "function_exists", {"path": "stubs.py", "symbol": "stub_not_impl"}, criterion_id="4"
        ),
    ]

    results = verify_criteria(criteria, project)
    assert results[0].passed is True
    assert results[1].passed is False
    assert "stub" in (results[1].actual_value or "")
    assert results[2].passed is False
    assert "stub" in (results[2].actual_value or "")
    assert results[3].passed is False
    assert "stub" in (results[3].actual_value or "")
