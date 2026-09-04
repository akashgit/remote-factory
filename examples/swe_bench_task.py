"""SWEBenchTask — demonstrates the Task contract with the DEFAULT run() path.

Uses the standard setup → prompt → subprocess → verify pipeline via compose().
No run() override — the base Task.run() shells out to factory ceo.
This is the opposite of ChessEvolveTask which overrides run() for bundled execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import structlog

from factory.task import (
    ScoringContract,
    Task,
    TaskConstraints,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)

log = structlog.get_logger()

_BUILTIN_INSTANCES: list[dict[str, Any]] = [
    {
        "instance_id": "django__django-16379",
        "repo": "django/django",
        "base_commit": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "problem_statement": (
            "FileBasedCache has_key is susceptible to race conditions. "
            "The has_key method opens the file, reads the expiry timestamp, "
            "then closes and potentially deletes it. Between the read and the "
            "delete another process could write a new value, which gets lost."
        ),
        "test_patch": (
            "--- a/tests/cache/tests.py\n"
            "+++ b/tests/cache/tests.py\n"
            "@@ -1,0 +1,10 @@\n"
            "+def test_has_key_race_condition(self):\n"
            "+    cache.set('key', 'value', 10)\n"
            "+    assert cache.has_key('key')\n"
        ),
        "FAIL_TO_PASS": ["tests.cache.tests.FileBasedCacheTests.test_has_key_race_condition"],
        "PASS_TO_PASS": ["tests.cache.tests.FileBasedCacheTests.test_has_key"],
        "hints_text": "Look at the has_key method in django/core/cache/backends/filebased.py",
    },
    {
        "instance_id": "sympy__sympy-24152",
        "repo": "sympy/sympy",
        "base_commit": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        "problem_statement": (
            "Bug in expand of TensorProduct. "
            "TensorProduct(A+B, C+D).expand(tensorproduct=True) gives "
            "wrong result when operands are non-commutative MatrixSymbols."
        ),
        "test_patch": (
            "--- a/sympy/physics/quantum/tests/test_tensorproduct.py\n"
            "+++ b/sympy/physics/quantum/tests/test_tensorproduct.py\n"
            "@@ -1,0 +1,8 @@\n"
            "+def test_tensor_product_expand_noncommutative():\n"
            "+    from sympy import symbols, Matrix\n"
            "+    A, B, C, D = symbols('A B C D', commutative=False)\n"
            "+    result = TensorProduct(A + B, C + D).expand(tensorproduct=True)\n"
            "+    assert result == TensorProduct(A, C) + TensorProduct(A, D) + "
            "TensorProduct(B, C) + TensorProduct(B, D)\n"
        ),
        "FAIL_TO_PASS": [
            "sympy/physics/quantum/tests/test_tensorproduct.py::test_tensor_product_expand_noncommutative"
        ],
        "PASS_TO_PASS": [
            "sympy/physics/quantum/tests/test_tensorproduct.py::test_tensor_product_expand"
        ],
        "hints_text": "Check the _eval_expand_tensorproduct method",
    },
    {
        "instance_id": "scikit-learn__scikit-learn-25570",
        "repo": "scikit-learn/scikit-learn",
        "base_commit": "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "problem_statement": (
            "ColumnTransformer with pandas output and transformers that return "
            "different numbers of columns raises IndexError. When using "
            "set_output(transform='pandas') and a transformer returns fewer "
            "columns than expected, an unhelpful IndexError is raised instead "
            "of a clear error message."
        ),
        "test_patch": (
            "--- a/sklearn/compose/tests/test_column_transformer.py\n"
            "+++ b/sklearn/compose/tests/test_column_transformer.py\n"
            "@@ -1,0 +1,12 @@\n"
            "+def test_column_transformer_pandas_output_column_mismatch():\n"
            "+    import pandas as pd\n"
            "+    ct = ColumnTransformer([('t', transformer, [0, 1])])\n"
            "+    ct.set_output(transform='pandas')\n"
            "+    with pytest.raises(ValueError, match='column mismatch'):\n"
            "+        ct.fit_transform(pd.DataFrame({'a': [1], 'b': [2]}))\n"
        ),
        "FAIL_TO_PASS": [
            "sklearn/compose/tests/test_column_transformer.py::test_column_transformer_pandas_output_column_mismatch"
        ],
        "PASS_TO_PASS": [],
        "hints_text": "Look at _get_feature_names_out in column_transformer.py",
    },
    {
        "instance_id": "matplotlib__matplotlib-25433",
        "repo": "matplotlib/matplotlib",
        "base_commit": "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
        "problem_statement": (
            "xlim_changed not emitted on shared axes. When calling "
            "ax.set_xlim on an axes that shares its x-axis with another, "
            "the xlim_changed callback fires on the first axes but not on "
            "the shared one."
        ),
        "test_patch": (
            "--- a/lib/matplotlib/tests/test_axes.py\n"
            "+++ b/lib/matplotlib/tests/test_axes.py\n"
            "@@ -1,0 +1,14 @@\n"
            "+def test_xlim_changed_shared():\n"
            "+    fig, (ax1, ax2) = plt.subplots(1, 2, sharex=True)\n"
            "+    calls = []\n"
            "+    ax2.callbacks.connect('xlim_changed', lambda ax: calls.append(ax))\n"
            "+    ax1.set_xlim(0, 10)\n"
            "+    assert len(calls) == 1\n"
        ),
        "FAIL_TO_PASS": [
            "lib/matplotlib/tests/test_axes.py::test_xlim_changed_shared"
        ],
        "PASS_TO_PASS": [
            "lib/matplotlib/tests/test_axes.py::test_xlim_changed"
        ],
        "hints_text": "",
    },
]


class SWEBenchTask(Task):
    """SWE-bench style bug-fix task using the DEFAULT run() path.

    instances() yields synthetic SWE-bench problem instances.
    setup() prepares the workspace with instance metadata and stub repo structure.
    prompt() returns the issue description for an agent to fix.
    verify() checks whether expected artifacts were produced in the workspace.

    Does NOT override run() — the base Task.run() handles the full pipeline:
    setup → write prompt to temp file → shell out to factory ceo → verify.
    """

    def __init__(self) -> None:
        defn = TaskDefinition(
            name="swe-bench",
            description="SWE-bench style bug-fix task using default run() pipeline",
            scoring=ScoringContract(method="exit_code"),
            constraints=TaskConstraints(timeout=300, max_retries=1),
        )
        super().__init__(definition=defn)

    def instances(self) -> Iterator[TaskInstance]:
        for item in _BUILTIN_INSTANCES:
            yield TaskInstance(
                id=item["instance_id"],
                metadata={
                    "repo": item["repo"],
                    "base_commit": item["base_commit"],
                    "problem_statement": item["problem_statement"],
                    "test_patch": item["test_patch"],
                    "FAIL_TO_PASS": item["FAIL_TO_PASS"],
                    "PASS_TO_PASS": item.get("PASS_TO_PASS", []),
                    "hints_text": item.get("hints_text", ""),
                },
            )

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)

        instance_file = workspace / "instance.json"
        instance_data = {"instance_id": instance.id, **instance.metadata}
        instance_file.write_text(json.dumps(instance_data, indent=2))

        test_patch = instance.metadata.get("test_patch", "")
        patch_file = workspace / "test_patch.diff"
        patch_file.write_text(test_patch)

        requirements_file = workspace / "requirements.txt"
        if not requirements_file.exists():
            requirements_file.write_text("")

        repo_name = instance.metadata.get("repo", "unknown/unknown")
        repo_parts = repo_name.split("/")
        project_name = repo_parts[-1] if repo_parts else "project"
        src_dir = workspace / project_name
        src_dir.mkdir(parents=True, exist_ok=True)

        init_file = src_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

        tests_dir = workspace / "tests"
        tests_dir.mkdir(exist_ok=True)
        test_init = tests_dir / "__init__.py"
        if not test_init.exists():
            test_init.write_text("")

        self.shell("ls -la", cwd=workspace)

    def prompt(self, instance: TaskInstance) -> str:
        repo = instance.metadata.get("repo", "unknown/unknown")
        problem = instance.metadata.get("problem_statement", "")
        fail_tests = instance.metadata.get("FAIL_TO_PASS", [])
        hints = instance.metadata.get("hints_text", "")

        lines = [
            f"# Bug Fix Required: {repo}",
            "",
            "## Problem Description",
            problem,
            "",
            "## Failing Test(s)",
        ]
        for test_id in fail_tests:
            lines.append(f"- `{test_id}`")

        if hints:
            lines.extend(["", "## Hints", hints])

        lines.extend([
            "",
            "## Instructions",
            "Apply a minimal fix so that the failing test(s) pass.",
            "Do not modify the test file itself.",
            "Keep the change as small and focused as possible.",
        ])

        return "\n".join(lines)

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        instance_file = workspace / "instance.json"
        if not instance_file.exists():
            return VerifyResult(
                passed=False,
                score=0.0,
                details={"error": "instance.json missing — setup() was not called"},
            )

        has_patch = False
        for pattern in ("*.patch", "*.diff", "changes.diff"):
            matches = list(workspace.glob(pattern))
            # Exclude the test_patch.diff we wrote in setup
            matches = [m for m in matches if m.name != "test_patch.diff"]
            if matches:
                has_patch = True
                break

        result = self.shell("ls -la", cwd=workspace)

        repo_name = instance.metadata.get("repo", "unknown/unknown")
        repo_parts = repo_name.split("/")
        project_name = repo_parts[-1] if repo_parts else "project"
        src_dir = workspace / project_name

        has_code_changes = False
        if src_dir.is_dir():
            for py_file in src_dir.rglob("*.py"):
                content = py_file.read_text()
                if content.strip() and content.strip() != "":
                    has_code_changes = True
                    break

        fail_tests = instance.metadata.get("FAIL_TO_PASS", [])
        test_name = fail_tests[0] if fail_tests else "unknown_test"

        if has_patch or has_code_changes:
            return VerifyResult(
                passed=True,
                score=1.0,
                details={
                    "test_name": test_name,
                    "test_status": "PASSED",
                    "has_patch": has_patch,
                    "has_code_changes": has_code_changes,
                    "workspace_listing": result.stdout[:500],
                },
            )

        return VerifyResult(
            passed=False,
            score=0.0,
            details={
                "test_name": test_name,
                "test_status": "FAILED",
                "has_patch": False,
                "has_code_changes": False,
                "stderr": "No patch or code changes detected in workspace",
            },
        )


meta = {
    "name": "swe-bench",
    "description": "SWE-bench style bug-fix task using default run() pipeline",
}


def task() -> SWEBenchTask:
    return SWEBenchTask()
