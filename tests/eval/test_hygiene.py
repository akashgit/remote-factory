"""Tests for factory.eval.hygiene — universal hygiene dimensions."""

import json
from unittest.mock import patch

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
    _find_sub_projects,
    compute_hygiene_results,
    eval_config_parser,
    eval_coverage,
    eval_guard_patterns,
    eval_lint,
    eval_security,
    eval_tests,
    eval_type_check,
)


class TestHygieneWeights:
    def test_weights_sum_to_one(self):
        total = sum(HYGIENE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_seven_dimensions(self):
        assert set(HYGIENE_WEIGHTS.keys()) == {
            "tests", "lint", "type_check", "coverage", "guard_patterns", "config_parser",
            "security",
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
    def test_returns_all_seven(self, tmp_path):
        results = compute_hygiene_results(tmp_path)
        assert len(results) == 7
        names = {r["name"] for r in results}
        assert names == {"tests", "lint", "type_check", "coverage", "guard_patterns", "config_parser", "security"}

    def test_all_have_required_keys(self, tmp_path):
        results = compute_hygiene_results(tmp_path)
        for r in results:
            assert "name" in r
            assert "score" in r
            assert "weight" in r
            assert "passed" in r
            assert "details" in r


class TestEvalSecurity:
    def test_no_scanner_returns_neutral(self, tmp_path):
        result = eval_security(tmp_path)
        assert result["name"] == "security"
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]

    def test_python_bandit_clean(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        bandit_output = json.dumps({"results": []})
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, bandit_output, "")
            result = eval_security(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "clean" in result["details"]

    def test_python_bandit_issues(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        bandit_output = json.dumps({
            "results": [
                {"issue_severity": "HIGH", "issue_text": "Use of exec"},
                {"issue_severity": "MEDIUM", "issue_text": "Hardcoded password"},
                {"issue_severity": "LOW", "issue_text": "Assert used"},
            ],
        })
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, bandit_output, "")
            result = eval_security(tmp_path)
        assert result["score"] == round(1.0 - 3 * 0.1, 4)
        assert result["passed"] is False
        assert "3 issues" in result["details"]

    def test_node_npm_audit_clean(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        audit_output = json.dumps({
            "metadata": {"vulnerabilities": {"low": 0, "moderate": 0, "high": 0, "critical": 0}},
        })
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (0, audit_output, "")
            result = eval_security(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "js" in result["details"]

    def test_node_npm_audit_vulnerabilities(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        audit_output = json.dumps({
            "metadata": {"vulnerabilities": {"low": 2, "moderate": 1, "high": 1, "critical": 0}},
        })
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, audit_output, "")
            result = eval_security(tmp_path)
        assert result["passed"] is False
        assert "4 vulnerabilities" in result["details"]

    def test_bandit_not_installed(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, "", "Command not found: bandit")
            result = eval_security(tmp_path)
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]

    def test_score_floor_at_zero(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        issues = [{"issue_severity": "HIGH", "issue_text": f"issue {i}"} for i in range(15)]
        bandit_output = json.dumps({"results": issues})
        with patch("factory.eval.hygiene._run_cmd") as mock:
            mock.return_value = (1, bandit_output, "")
            result = eval_security(tmp_path)
        assert result["score"] == 0.0
