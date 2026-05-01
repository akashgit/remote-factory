"""Tests for factory.eval.hygiene — universal hygiene dimensions."""

import subprocess
from unittest.mock import patch

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
    _find_sub_projects,
    _run_cmd,
    compute_hygiene_results,
    eval_config_parser,
    eval_coverage,
    eval_guard_patterns,
    eval_lint,
    eval_tests,
    eval_type_check,
)


class TestHygieneWeights:
    def test_weights_sum_to_one(self):
        total = sum(HYGIENE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_six_dimensions(self):
        assert set(HYGIENE_WEIGHTS.keys()) == {
            "tests", "lint", "type_check", "coverage", "guard_patterns", "config_parser",
        }


class TestFindSubProjects:
    def test_single_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        roots = _find_sub_projects(tmp_path)
        assert tmp_path in roots

    def test_multi_repo(self, tmp_path):
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text("{}\n")
        roots = _find_sub_projects(tmp_path)
        assert len(roots) == 2

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "pyproject.toml").write_text("[project]\n")
        roots = _find_sub_projects(tmp_path)
        assert all(".venv" not in str(r) for r in roots)

    def test_empty_dir_returns_project_path(self, tmp_path):
        roots = _find_sub_projects(tmp_path)
        assert roots == [tmp_path]


class TestEvalTests:
    def test_no_test_suite_returns_neutral(self, tmp_path):
        result = eval_tests(tmp_path)
        assert result["name"] == "tests"
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]

    def test_python_project_with_tests(self, python_project):
        result = eval_tests(python_project)
        assert result["name"] == "tests"
        # Should find and run the test
        assert result["score"] >= 0.0


class TestEvalLint:
    def test_no_linter_returns_neutral(self, tmp_path):
        result = eval_lint(tmp_path)
        assert result["name"] == "lint"
        assert result["score"] == 0.5

    def test_weight_matches(self, tmp_path):
        result = eval_lint(tmp_path)
        assert result["weight"] == HYGIENE_WEIGHTS["lint"]


class TestEvalTypeCheck:
    def test_no_type_checker_returns_neutral(self, tmp_path):
        result = eval_type_check(tmp_path)
        assert result["name"] == "type_check"
        assert result["score"] == 0.5


class TestEvalCoverage:
    def test_no_coverage_tool_returns_neutral(self, tmp_path):
        result = eval_coverage(tmp_path)
        assert result["name"] == "coverage"
        assert result["score"] == 0.5


class TestEvalGuardPatterns:
    def test_basic_patterns(self, tmp_path):
        result = eval_guard_patterns(tmp_path)
        assert result["name"] == "guard_patterns"
        assert result["score"] > 0.0

    def test_with_factory_config(self, tmp_path):
        import json
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        config = {"scope": ["src/**/*.py", "tests/**/*.py"], "goal": "", "guards": [],
                  "eval_command": "", "eval_threshold": 0.8, "constraints": []}
        (factory_dir / "config.json").write_text(json.dumps(config))
        result = eval_guard_patterns(tmp_path)
        assert result["name"] == "guard_patterns"


class TestEvalConfigParser:
    def test_no_factory_md_returns_neutral(self, tmp_path):
        result = eval_config_parser(tmp_path)
        assert result["name"] == "config_parser"
        assert result["score"] == 0.5

    def test_valid_factory_md(self, tmp_path):
        (tmp_path / "factory.md").write_text(
            "# Factory Config\n\n## Goal\nTest project\n\n"
            "## Scope\n### Modifiable\n- src/**\n\n"
            "## Eval\n### Command\n```\npython eval/score.py\n```\n"
            "### Threshold\n0.8\n"
        )
        (tmp_path / ".factory").mkdir()
        result = eval_config_parser(tmp_path)
        assert result["name"] == "config_parser"
        assert result["score"] > 0.0


class TestComputeHygieneResults:
    def test_returns_all_six(self, tmp_path):
        results = compute_hygiene_results(tmp_path)
        assert len(results) == 6
        names = {r["name"] for r in results}
        assert names == {"tests", "lint", "type_check", "coverage", "guard_patterns", "config_parser"}

    def test_all_have_required_keys(self, tmp_path):
        results = compute_hygiene_results(tmp_path)
        for r in results:
            assert "name" in r
            assert "score" in r
            assert "weight" in r
            assert "passed" in r
            assert "details" in r


class TestRunCmd:
    def test_timeout_returns_error(self, tmp_path):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["cmd"], 120)):
            rc, stdout, stderr = _run_cmd(["cmd"], tmp_path)
        assert rc == 1
        assert "Timed out" in stderr

    def test_command_not_found(self, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            rc, stdout, stderr = _run_cmd(["nonexistent"], tmp_path)
        assert rc == 1
        assert "Command not found" in stderr

    def test_generic_exception(self, tmp_path):
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            rc, stdout, stderr = _run_cmd(["cmd"], tmp_path)
        assert rc == 1
        assert "boom" in stderr


class TestEvalTestsMultiLang:
    def test_node_project_with_tests(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, "Tests: 5 passed, 0 failed", "")
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "js" in result["details"]

    def test_rust_project_with_tests(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, "test result: ok. 12 passed; 0 failed", "")
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert "rs" in result["details"]

    def test_go_project_passing(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, "ok  \texample/pkg\t0.5s\nok  \texample/cmd\t0.3s\n", "")
            result = eval_tests(tmp_path)
        assert result["score"] > 0.0
        assert result["passed"] is True
        assert "go" in result["details"]

    def test_go_project_failing(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "FAIL\texample/pkg\t0.5s\n", "")
            result = eval_tests(tmp_path)
        assert result["passed"] is False
        assert "go" in result["details"]

    def test_mixed_project_aggregates(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}\n")

        def mock_run_cmd(cmd, cwd, timeout=120):
            if "pytest" in cmd:
                return (0, "3 passed", "")
            if "npm" in cmd:
                return (0, "Tests: 2 passed, 1 failed", "")
            return (1, "", "")

        with patch("factory.eval.hygiene._run_cmd", side_effect=mock_run_cmd):
            result = eval_tests(tmp_path)
        assert result["score"] == round(5 / 6, 4)


class TestEvalTestsScoring:
    def test_all_tests_fail(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "Tests: 0 passed, 5 failed", "")
            result = eval_tests(tmp_path)
        assert result["score"] == 0.0
        assert result["passed"] is False

    def test_partial_pass(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "Tests: 3 passed, 1 failed", "")
            result = eval_tests(tmp_path)
        assert result["score"] == 0.75
        assert result["passed"] is False


class TestEvalLintMultiLang:
    def test_node_lint_clean(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, "", "")
            result = eval_lint(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "js" in result["details"]

    def test_node_lint_errors(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "Error - foo\nError - bar\nError - baz\n", "")
            result = eval_lint(tmp_path)
        assert result["passed"] is False
        assert "3" in result["details"]

    def test_rust_lint_clean(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, "", "")
            result = eval_lint(tmp_path)
        assert result["score"] == 1.0
        assert "rs" in result["details"]

    def test_rust_lint_errors(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "", "error[E0308]: mismatch\nerror[E0599]: no method\n")
            result = eval_lint(tmp_path)
        assert result["passed"] is False
        assert "2" in result["details"]


class TestEvalLintScoring:
    def test_partial_credit(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "Error - a\nError - b\nError - c\n", "")
            result = eval_lint(tmp_path)
        assert result["score"] == round(1.0 - 3 * 0.1, 4)

    def test_score_floor_at_zero(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        errors = "\n".join(f"Error - e{i}" for i in range(15))
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, errors, "")
            result = eval_lint(tmp_path)
        assert result["score"] == 0.0


class TestEvalTypeCheckMultiLang:
    def test_node_typecheck_clean(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, "", "")
            result = eval_type_check(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "ts" in result["details"]

    def test_node_typecheck_errors(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (
                1,
                "src/index.ts(1,1): error TS2304: Cannot find name\n"
                "src/index.ts(5,3): error TS7006: Parameter implicitly has 'any' type\n",
                "",
            )
            result = eval_type_check(tmp_path)
        assert result["passed"] is False
        assert "2" in result["details"]
