"""End-to-end test: temp git repo with passing/failing hypotheses."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from factory.plan_check.cli import run_plan_check
from factory.plan_check.reporter import to_json

SAMPLE_PLAN = """\
## Build Plan

### Phase 1
#### H1: Implement greeting module
- **Category:** EXPLORE
- **Growth dimension:** capability_surface
- **What:**
  - Create `src/greeter.py` with function `greet()`
  - Tests in `tests/test_greeter.py`:
    - `test_greet_returns_string`
- **Expected impact:** capability_surface 0.0 → 0.5
- **Priority:** high

### Phase 2
#### H2: Implement broken farewell module
- **Category:** EXPLORE
- **Growth dimension:** capability_surface
- **What:**
  - Create `src/farewell.py` with function `farewell()`
  - Tests in `tests/test_farewell.py`:
    - `test_farewell_returns_string`
- **Expected impact:** capability_surface 0.5 → 0.8
- **Priority:** high
"""


@pytest.fixture
def e2e_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        capture_output=True, check=True, cwd=str(tmp_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        capture_output=True, check=True, cwd=str(tmp_path),
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "greeter.py").write_text(
        "def greet() -> str:\n"
        "    return 'hello'\n"
    )
    (tmp_path / "src" / "farewell.py").write_text(
        "def farewell():\n"
        "    pass\n"
    )

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_greeter.py").write_text(
        "from src.greeter import greet\n\n"
        "def test_greet_returns_string():\n"
        "    assert isinstance(greet(), str)\n"
    )
    (tmp_path / "tests" / "test_farewell.py").write_text(
        "from src.farewell import farewell\n\n"
        "def test_farewell_returns_string():\n"
        "    result = farewell()\n"
        "    assert isinstance(result, str), 'farewell() should return a string'\n"
    )

    strategy_dir = tmp_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "current.md").write_text(SAMPLE_PLAN)

    (tmp_path / "conftest.py").write_text(
        "import sys\n"
        "sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))\n"
    )

    subprocess.run(
        ["git", "add", "."],
        capture_output=True, check=True, cwd=str(tmp_path),
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        capture_output=True, check=True, cwd=str(tmp_path),
    )

    return tmp_path


def test_e2e_h1_passes_h2_fails(e2e_project: Path):
    report = run_plan_check(
        project_path=e2e_project,
        strategy_path=e2e_project / ".factory" / "strategy" / "current.md",
        output_dir=e2e_project / ".factory" / "reports",
    )

    verdicts = {v.hypothesis_id: v for v in report.hypotheses}

    h1 = verdicts["H1"]
    file_criteria = [c for c in h1.criteria if c.criterion.verification_method == "file_exists"]
    func_criteria = [c for c in h1.criteria if c.criterion.verification_method == "function_exists"]
    assert all(c.passed for c in file_criteria), "H1 file deliverables should pass"
    assert all(c.passed for c in func_criteria), "H1 function deliverables should pass"

    h2 = verdicts["H2"]
    assert not h2.passed, "H2 should fail (farewell is a stub)"
    stub_failures = [
        c for c in h2.criteria
        if not c.passed and c.actual_value and "stub" in c.actual_value
    ]
    assert len(stub_failures) >= 1, "H2 should have at least one stub failure"

    json_str = to_json(report)
    parsed = json.loads(json_str)
    assert parsed["redirect_needed"] is True
    assert any(
        u["hypothesis_id"] == "H2"
        for u in parsed["unsatisfied_criteria"]
    )


def test_e2e_exit_code_is_1_when_failures_exist(e2e_project: Path):
    report = run_plan_check(
        project_path=e2e_project,
        strategy_path=e2e_project / ".factory" / "strategy" / "current.md",
        output_dir=e2e_project / ".factory" / "reports",
    )
    assert not report.summary.all_passed


def test_e2e_report_files_created(e2e_project: Path):
    output_dir = e2e_project / ".factory" / "reports"
    run_plan_check(
        project_path=e2e_project,
        strategy_path=e2e_project / ".factory" / "strategy" / "current.md",
        output_dir=output_dir,
    )
    assert (output_dir / "acceptance-verification.md").exists()
    assert (output_dir / "acceptance-verification.json").exists()

    md_content = (output_dir / "acceptance-verification.md").read_text()
    assert "Unsatisfied Acceptance Criteria" in md_content
    assert "REDIRECT NEEDED" in md_content
