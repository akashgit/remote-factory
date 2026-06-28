"""Tests for factory.eval.hygiene — universal hygiene dimensions."""

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
    _find_sub_projects,
    compute_hygiene_results,
    eval_config_parser,
    eval_coverage,
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
            "tests", "lint", "type_check", "coverage", "config_parser",
            "architecture",
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
        assert names == {"tests", "lint", "type_check", "coverage", "config_parser", "architecture"}

    def test_all_have_required_keys(self, tmp_path):
        results = compute_hygiene_results(tmp_path)
        for r in results:
            assert "name" in r
            assert "score" in r
            assert "weight" in r
            assert "passed" in r
            assert "details" in r

    def test_accepts_test_timeout_parameter(self, tmp_path):
        """compute_hygiene_results should accept test_timeout parameter."""
        results = compute_hygiene_results(tmp_path, test_timeout=900)
        assert len(results) == 6
