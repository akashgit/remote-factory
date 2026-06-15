"""Characterization tests for hygiene eval functions — snapshot tests with mocked subprocess."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
    eval_coverage,
    eval_lint,
    eval_tests,
    eval_type_check,
)
from factory.eval.languages import _aggregate
from factory.eval.languages.base import EvalFragment, _run_cmd


def _make_run_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock subprocess.run result."""
    class _Result:
        def __init__(self, rc, out, err):
            self.returncode = rc
            self.stdout = out
            self.stderr = err
    return _Result(returncode, stdout, stderr)


# ── Python characterization ──────────────────────────────────────


class TestPythonTests:
    def test_passing_tests(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="10 passed, 2 failed in 1.5s\n", returncode=1
            )
            result = eval_tests(tmp_path)
        assert result["name"] == "tests"
        assert result["score"] == round(10 / 12, 4)
        assert result["passed"] is False
        assert result["weight"] == HYGIENE_WEIGHTS["tests"]
        assert "10 passed, 2 failed" in result["details"]

    def test_all_passing(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="5 passed in 0.5s\n", returncode=0
            )
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True

    def test_no_results(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="no tests ran\n", returncode=0
            )
            result = eval_tests(tmp_path)
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]


class TestPythonLint:
    def test_clean(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            result = eval_lint(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "clean" in result["details"]

    def test_with_errors(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Found 3 errors.\n", returncode=1
            )
            result = eval_lint(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 3 * 0.1), 4)
        assert result["passed"] is False
        assert "3 errors" in result["details"]


class TestPythonTypeCheck:
    def test_clean(self, tmp_path):
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Success: no issues found\n", returncode=0
            )
            result = eval_type_check(tmp_path)
        assert result["score"] == 1.0
        assert "clean" in result["details"]

    def test_sorted_dir_ordering(self, tmp_path):
        """Verify that sorted(sp.iterdir()) picks the alphabetically first package."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        alpha = tmp_path / "alpha"
        alpha.mkdir()
        (alpha / "__init__.py").write_text("")
        beta = tmp_path / "beta"
        beta.mkdir()
        (beta / "__init__.py").write_text("")

        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            eval_type_check(tmp_path)
            cmd = mock_run.call_args[0][0]
            assert cmd[-1] == "alpha"

    def test_with_errors(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Found 5 errors in 3 files\n", returncode=1
            )
            result = eval_type_check(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 5 * 0.05), 4)
        assert result["passed"] is False


class TestPythonCoverage:
    def test_coverage_result(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="3 passed\nTOTAL      100     20     80%\n", returncode=0
            )
            result = eval_coverage(tmp_path)
        assert result["name"] == "coverage"
        assert result["score"] == round(80 / 100.0, 4)
        assert result["passed"] is True
        assert "80%" in result["details"]

    def test_sorted_dir_ordering_coverage(self, tmp_path):
        """Coverage also uses sorted(sp.iterdir()) for target."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "main.py").write_text("")
        alpha = tmp_path / "alpha"
        alpha.mkdir()
        (alpha / "__init__.py").write_text("")
        beta = tmp_path / "beta"
        beta.mkdir()
        (beta / "__init__.py").write_text("")

        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="3 passed\nTOTAL      100     20     80%\n", returncode=0
            )
            eval_coverage(tmp_path)
            cmd = mock_run.call_args[0][0]
            assert "--cov=alpha" in cmd


# ── Node characterization ────────────────────────────────────────


class TestNodeTests:
    def test_jest_output(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Tests: 8 passed, 1 failed\n", returncode=1
            )
            result = eval_tests(tmp_path)
        assert result["score"] == round(8 / 9, 4)
        assert result["passed"] is False
        assert "(js)" in result["details"]


class TestNodeLint:
    def test_eslint_errors(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="file.js: line 1, col 1, Error - msg\nfile.js: line 2, col 1, Error - msg\n",
                returncode=1,
            )
            result = eval_lint(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 2 * 0.1), 4)
        assert "2 errors" in result["details"]

    def test_eslint_error_fallback(self, tmp_path):
        """When no 'Error -' found, count defaults to max(0, 1) = 1."""
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="some error output\n", returncode=1
            )
            result = eval_lint(tmp_path)
        assert "1 errors" in result["details"]


class TestNodeTypeCheck:
    def test_tsc_errors(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.ts").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="src/a.ts(1,1): error TS2304: blah\nsrc/b.ts(2,1): error TS2304: blah\n",
                returncode=1,
            )
            result = eval_type_check(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 2 * 0.05), 4)
        assert "2 errors" in result["details"]

    def test_tsc_error_fallback(self, tmp_path):
        """When no 'error TS' found, count defaults to max(0, 1) = 1."""
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.ts").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="some error\n", returncode=1
            )
            result = eval_type_check(tmp_path)
        assert "1 errors" in result["details"]


# ── Rust characterization ────────────────────────────────────────


class TestRustTests:
    def test_cargo_test_output(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="test result: ok. 3 passed; 1 failed; 0 ignored\n",
                returncode=1,
            )
            result = eval_tests(tmp_path)
        assert result["score"] == round(3 / 4, 4)
        assert "(rs)" in result["details"]
        cmd = mock_run.call_args[0][0]
        assert cmd == ["cargo", "test"]


class TestRustLint:
    def test_clippy_clean(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            result = eval_lint(tmp_path)
        assert result["score"] == 1.0
        assert "clean" in result["details"]

    def test_clippy_errors(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stderr="error[E0308]: mismatch\nerror[E0599]: no method\n",
                returncode=1,
            )
            result = eval_lint(tmp_path)
        assert "2 errors" in result["details"]


# ── Go characterization ──────────────────────────────────────────


class TestGoTests:
    def test_go_test_passing(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="ok  \ttest/pkg1\t0.5s\nok  \ttest/pkg2\t0.3s\n",
                returncode=0,
            )
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "(go)" in result["details"]

    def test_go_test_max_ok_count(self, tmp_path):
        """max(ok_count, 1) — even with 0 ok lines, passed count is at least 1."""
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="testing done\n",
                returncode=0,
            )
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True

    def test_go_test_failing(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="FAIL\ttest/pkg\t0.5s\n",
                returncode=1,
            )
            result = eval_tests(tmp_path)
        assert result["passed"] is False
        assert "(go)" in result["details"]

    def test_go_test_no_fail_no_ok(self, tmp_path):
        """If rc != 0 and no FAIL, Go returns None → neutral."""
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="some error\n", returncode=1
            )
            result = eval_tests(tmp_path)
        assert result["score"] == 0.5


# ── Neutral score characterization ───────────────────────────────


class TestNeutralScores:
    def test_no_project_returns_neutral_tests(self, tmp_path):
        result = eval_tests(tmp_path)
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]

    def test_no_project_returns_neutral_lint(self, tmp_path):
        result = eval_lint(tmp_path)
        assert result["score"] == 0.5

    def test_no_project_returns_neutral_type_check(self, tmp_path):
        result = eval_type_check(tmp_path)
        assert result["score"] == 0.5

    def test_no_project_returns_neutral_coverage(self, tmp_path):
        result = eval_coverage(tmp_path)
        assert result["score"] == 0.5


# ── EvalFragment clamping ────────────────────────────────────────


class TestEvalFragmentClamping:
    def test_score_clamped_to_zero(self):
        from factory.eval.languages.base import EvalFragment
        frag = EvalFragment(passed=0, failed=10, score=-0.5, details="test")
        assert frag.score == 0.0

    def test_score_clamped_to_one(self):
        from factory.eval.languages.base import EvalFragment
        frag = EvalFragment(passed=10, failed=0, score=1.5, details="test")
        assert frag.score == 1.0

    def test_score_in_range_unchanged(self):
        from factory.eval.languages.base import EvalFragment
        frag = EvalFragment(passed=5, failed=5, score=0.5, details="test")
        assert frag.score == 0.5


# ── Go lint / type_check / coverage ─────────────────────────────


class TestGoLint:
    def test_go_vet_clean(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            result = eval_lint(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "clean" in result["details"]

    def test_go_vet_errors(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stderr="main.go:10:5: unreachable code\nmain.go:20:3: unused variable\n",
                returncode=1,
            )
            result = eval_lint(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 2 * 0.1), 4)
        assert "2 errors" in result["details"]

    def test_go_vet_error_fallback(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stderr="some error\n", returncode=1,
            )
            result = eval_lint(tmp_path)
        assert "1 errors" in result["details"]


class TestGoTypeCheck:
    def test_go_build_clean(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            result = eval_type_check(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "clean" in result["details"]

    def test_go_build_errors(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stderr="main.go:5:10: undefined: foo\nmain.go:12:3: syntax error\n",
                returncode=1,
            )
            result = eval_type_check(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 2 * 0.05), 4)
        assert "2 errors" in result["details"]


class TestGoCoverage:
    def test_go_coverage_result(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="ok  \ttest/pkg\t0.5s\tcoverage: 75.0% of statements\n",
                returncode=0,
            )
            result = eval_coverage(tmp_path)
        assert result["name"] == "coverage"
        assert result["score"] == round(75 / 100.0, 4)
        assert "75%" in result["details"]

    def test_go_coverage_no_coverage_line(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="ok  \ttest/pkg\t0.5s\n",
                returncode=0,
            )
            result = eval_coverage(tmp_path)
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]


# ── Rust type_check / coverage ──────────────────────────────────


class TestRustTypeCheck:
    def test_cargo_check_clean(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            result = eval_type_check(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "clean" in result["details"]

    def test_cargo_check_errors(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stderr="error[E0308]: mismatched types\nerror[E0599]: no method\n",
                returncode=1,
            )
            result = eval_type_check(tmp_path)
        assert result["score"] == round(max(0.0, 1.0 - 2 * 0.05), 4)
        assert "2 errors" in result["details"]


class TestRustCoverage:
    def test_rust_coverage_result(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="test result: ok. 5 passed; 0 failed; 0 ignored\n85.50% coverage, 171/200 lines covered\n",
                returncode=0,
            )
            result = eval_coverage(tmp_path)
        assert result["name"] == "coverage"
        assert result["score"] == round(85.5 / 100.0, 4)
        assert "86%" in result["details"]

    def test_rust_coverage_no_coverage_line(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="test result: ok. 3 passed; 0 failed\n",
                returncode=0,
            )
            result = eval_coverage(tmp_path)
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]


# ── Node coverage / type_check clean ────────────────────────────


class TestNodeCoverage:
    def test_node_coverage_result(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Tests: 5 passed, 0 failed\nStatements   : 72.5% ( 100/138 )\n",
                returncode=0,
            )
            result = eval_coverage(tmp_path)
        assert result["name"] == "coverage"
        assert result["score"] == round(72.5 / 100.0, 4)
        assert "72%" in result["details"]

    def test_node_coverage_no_statements(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Tests: 3 passed\n", returncode=0,
            )
            result = eval_coverage(tmp_path)
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]


class TestNodeTypeCheckClean:
    def test_tsc_clean(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.ts").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(returncode=0)
            result = eval_type_check(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "clean" in result["details"]


# ── Combined method tests ──────────────────────────────────────


class TestGoTestsWithCoverage:
    def test_both_fragments(self, tmp_path):
        from factory.eval.languages.go import GoEvaluator
        (tmp_path / "go.mod").write_text("module test\n")
        evaluator = GoEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="ok  \ttest/pkg\t0.5s\tcoverage: 80.0% of statements\n",
                returncode=0,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.score == 1.0
        assert test_frag.passed == 1
        assert cov_frag is not None
        assert cov_frag.coverage_pct == 80.0
        assert cov_frag.score == 0.8

    def test_failing_no_coverage(self, tmp_path):
        from factory.eval.languages.go import GoEvaluator
        (tmp_path / "go.mod").write_text("module test\n")
        evaluator = GoEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="FAIL\ttest/pkg\t0.5s\n", returncode=1,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.score == 0.0
        assert test_frag.failed == 1
        assert cov_frag is None

    def test_multiple_packages(self, tmp_path):
        from factory.eval.languages.go import GoEvaluator
        (tmp_path / "go.mod").write_text("module test\n")
        evaluator = GoEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout=(
                    "ok  \ttest/pkg1\t0.5s\tcoverage: 80.0% of statements\n"
                    "ok  \ttest/pkg2\t0.3s\tcoverage: 60.0% of statements\n"
                ),
                returncode=0,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 2
        assert cov_frag is not None
        assert cov_frag.coverage_pct == 70.0


class TestNodeTestsWithCoverage:
    def test_both_fragments(self, tmp_path):
        from factory.eval.languages.node import NodeEvaluator
        (tmp_path / "package.json").write_text("{}\n")
        evaluator = NodeEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Tests: 10 passed, 2 failed\nStatements   : 85.0% ( 170/200 )\n",
                returncode=1,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 10
        assert test_frag.failed == 2
        assert test_frag.score == pytest.approx(10 / 12)
        assert cov_frag is not None
        assert cov_frag.coverage_pct == 85.0

    def test_no_tests_no_coverage(self, tmp_path):
        from factory.eval.languages.node import NodeEvaluator
        (tmp_path / "package.json").write_text("{}\n")
        evaluator = NodeEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="No tests found\n", returncode=0,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is None
        assert cov_frag is None


class TestRustTestsWithCoverage:
    def test_both_fragments(self, tmp_path):
        from factory.eval.languages.rust import RustEvaluator
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        evaluator = RustEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="test result: ok. 5 passed; 1 failed; 0 ignored\n75.00% coverage, 150/200 lines covered\n",
                returncode=1,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 5
        assert test_frag.failed == 1
        assert test_frag.score == pytest.approx(5 / 6)
        assert cov_frag is not None
        assert cov_frag.coverage_pct == 75.0
        assert cov_frag.score == 0.75

    def test_no_coverage_line(self, tmp_path):
        from factory.eval.languages.rust import RustEvaluator
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        evaluator = RustEvaluator()
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="test result: ok. 3 passed; 0 failed; 0 ignored\n",
                returncode=0,
            )
            test_frag, cov_frag = evaluator.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 3
        assert test_frag.score == 1.0
        assert cov_frag is None


# ── _run_cmd error paths ────────────────────────────────────────


class TestRunCmd:
    def test_timeout(self):
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["test"], timeout=300)
            rc, stdout, stderr = _run_cmd(["test", "cmd"], Path("/tmp"))
        assert rc == 1
        assert stdout == ""
        assert "Timed out after 300s" in stderr

    def test_file_not_found(self):
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            rc, stdout, stderr = _run_cmd(["nonexistent"], Path("/tmp"))
        assert rc == 1
        assert stdout == ""
        assert "Command not found: nonexistent" in stderr

    def test_generic_exception(self):
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("something broke")
            rc, stdout, stderr = _run_cmd(["cmd"], Path("/tmp"))
        assert rc == 1
        assert stdout == ""
        assert stderr == "something broke"

    def test_debug_log_on_failure(self):
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stderr="some error output", returncode=1
            )
            rc, stdout, stderr = _run_cmd(["failing", "cmd"], Path("/tmp"))
        assert rc == 1
        assert stderr == "some error output"


# ── PythonEvaluator combined method ───────────────────────────


class TestPythonCombinedMethod:
    def test_returns_both_fragments(self, tmp_path):
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="5 passed in 0.5s\nTOTAL      100     20     80%\n",
                returncode=0,
            )
            test_frag, cov_frag = ev.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 5
        assert test_frag.failed == 0
        assert cov_frag is not None
        assert cov_frag.score == 0.8
        assert cov_frag.coverage_pct == 80

    def test_single_subprocess_call(self, tmp_path):
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="3 passed\nTOTAL      50     10     80%\n", returncode=0
            )
            ev.run_tests_with_coverage(tmp_path)
            mock_run.assert_called_once()

    def test_includes_cov_flags(self, tmp_path):
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="3 passed\nTOTAL      50     10     80%\n", returncode=0
            )
            ev.run_tests_with_coverage(tmp_path)
            cmd = mock_run.call_args[0][0]
            assert "--cov=mypkg" in cmd
            assert "--cov-report=term" in cmd

    def test_coverage_without_tests_returns_no_coverage(self, tmp_path):
        """If pytest produces coverage but no test results (collection error), both are None."""
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="no tests ran\nTOTAL      100     20     80%\n",
                returncode=1,
            )
            test_frag, cov_frag = ev.run_tests_with_coverage(tmp_path)
        assert test_frag is None
        assert cov_frag is None

    def test_no_instance_state(self):
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        assert not hasattr(ev, "_cached_outputs")


# ── _aggregate unknown dimension ────────────────────────────────


# ── GoEvaluator.run_tests_with_coverage ────────────────────────


class TestGoRunTestsWithCoverage:
    def test_returns_test_fragment_and_none(self, tmp_path):
        from factory.eval.languages.go import GoEvaluator

        ev = GoEvaluator()
        (tmp_path / "go.mod").write_text("module test\n")
        (tmp_path / "main.go").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="ok  \ttest/pkg1\t0.5s\n", returncode=0
            )
            test_frag, cov_frag = ev.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed >= 1
        assert test_frag.score == 1.0
        assert cov_frag is None


# ── NodeEvaluator.run_tests_with_coverage ──────────────────────


class TestNodeRunTestsWithCoverage:
    def test_returns_test_fragment_and_none(self, tmp_path):
        from factory.eval.languages.node import NodeEvaluator

        ev = NodeEvaluator()
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "index.js").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="Tests: 5 passed, 1 failed\n", returncode=1
            )
            test_frag, cov_frag = ev.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 5
        assert test_frag.failed == 1
        assert cov_frag is None


# ── RustEvaluator.run_tests_with_coverage ──────────────────────


class TestRustRunTestsWithCoverage:
    def test_returns_test_fragment_and_none(self, tmp_path):
        from factory.eval.languages.rust import RustEvaluator

        ev = RustEvaluator()
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("")
        with patch("factory.eval.languages.base.subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result(
                stdout="test result: ok. 3 passed; 0 failed; 0 ignored\n",
                returncode=0,
            )
            test_frag, cov_frag = ev.run_tests_with_coverage(tmp_path)
        assert test_frag is not None
        assert test_frag.passed == 3
        assert test_frag.failed == 0
        assert cov_frag is None


# ── _collect_test_and_coverage ─────────────────────────────────


class TestCollectTestAndCoverage:
    def test_returns_correct_tuple(self, tmp_path):
        from factory.eval.hygiene import _collect_test_and_coverage

        test_frag = EvalFragment(passed=5, failed=0, score=1.0, details="test: passed")
        cov_frag = EvalFragment(passed=1, failed=0, score=0.8, details="80%", coverage_pct=80)

        class FakeEvaluator:
            name = "fake"

            def detect(self, p):
                return True

            def run_tests_with_coverage(self, p):
                return test_frag, cov_frag

        with (
            patch("factory.eval.hygiene.detect_languages", return_value=[FakeEvaluator()]),
            patch("factory.eval.hygiene._find_sub_projects", return_value=[tmp_path]),
        ):
            test_result, cov_result = _collect_test_and_coverage(tmp_path)

        assert test_result["name"] == "tests"
        assert test_result["score"] == 1.0
        assert test_result["passed"] is True
        assert cov_result["name"] == "coverage"
        assert cov_result["score"] == 0.8

    def test_returns_neutral_when_no_fragments(self, tmp_path):
        from factory.eval.hygiene import _collect_test_and_coverage

        class EmptyEvaluator:
            name = "empty"

            def detect(self, p):
                return True

            def run_tests_with_coverage(self, p):
                return None, None

        with (
            patch("factory.eval.hygiene.detect_languages", return_value=[EmptyEvaluator()]),
            patch("factory.eval.hygiene._find_sub_projects", return_value=[tmp_path]),
        ):
            test_result, cov_result = _collect_test_and_coverage(tmp_path)

        assert test_result["score"] == 0.5
        assert "Not detected" in test_result["details"]
        assert cov_result["score"] == 0.5
        assert "Not detected" in cov_result["details"]


# ── _aggregate unknown dimension ────────────────────────────────


class TestAggregateUnknownDimension:
    def test_raises_value_error(self):
        fragment = EvalFragment(passed=1, failed=0, score=1.0, details="test")
        with pytest.raises(ValueError, match="Unknown dimension"):
            _aggregate([fragment], "nonexistent")
