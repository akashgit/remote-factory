"""Tests for factory.eval.hygiene — universal hygiene dimensions."""

from unittest.mock import patch

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
    _detect_java_project,
    _find_sub_projects,
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


class TestRustWorkspaceAggregation:
    """Tests for multi-crate cargo workspace test result aggregation."""

    WORKSPACE_OUTPUT = (
        "running 5 tests\n"
        "test tests::test_a ... ok\n"
        "test tests::test_b ... ok\n"
        "test tests::test_c ... ok\n"
        "test tests::test_d ... ok\n"
        "test tests::test_e ... ok\n"
        "\n"
        "test result: ok. 5 passed; 0 failed; 0 ignored\n"
        "\n"
        "running 10 tests\n"
        "test tests::test_f ... ok\n"
        "test result: ok. 10 passed; 0 failed; 0 ignored\n"
        "\n"
        "running 3 tests\n"
        "test tests::test_g ... FAILED\n"
        "test result: FAILED. 2 passed; 1 failed; 0 ignored\n"
    )

    def test_aggregates_multiple_crates(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers = ['a', 'b', 'c']\n")
        with (
            patch("factory.eval.hygiene._run_cmd", return_value=(1, self.WORKSPACE_OUTPUT, "")),
            patch("shutil.which", return_value="/usr/bin/cargo"),
        ):
            result = eval_tests(tmp_path)
        assert result["name"] == "tests"
        # 5 + 10 + 2 = 17 passed, 1 failed
        assert result["score"] == round(17 / 18, 4)
        assert result["passed"] is False
        assert "17 passed" in result["details"]
        assert "1 failed" in result["details"]

    def test_all_passing_workspace(self, tmp_path):
        output = (
            "test result: ok. 15 passed; 0 failed; 0 ignored\n"
            "test result: ok. 20 passed; 0 failed; 0 ignored\n"
        )
        (tmp_path / "Cargo.toml").write_text("[workspace]\n")
        with (
            patch("factory.eval.hygiene._run_cmd", return_value=(0, output, "")),
            patch("shutil.which", return_value="/usr/bin/cargo"),
        ):
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True

    def test_cargo_not_on_path_warns_and_skips(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname='test'\n")
        with (
            patch("shutil.which", return_value=None),
            patch("factory.eval.hygiene.log") as mock_log,
        ):
            result = eval_tests(tmp_path)
        mock_log.warning.assert_called_once()
        call_kwargs = mock_log.warning.call_args
        assert "cargo_not_found" in call_kwargs.args or "cargo_not_found" == call_kwargs.args[0]
        # No tests ran, should be neutral
        assert result["score"] == 0.5


class TestNodeMonorepoAggregation:
    """Tests for Node/Jest monorepo test result aggregation."""

    MONOREPO_OUTPUT = (
        "Tests: 12 passed, 0 failed, 12 total\n"
        "Tests: 8 passed, 2 failed, 10 total\n"
    )

    def test_aggregates_multiple_suites(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "monorepo"}\n')
        with patch("factory.eval.hygiene._run_cmd", return_value=(1, self.MONOREPO_OUTPUT, "")):
            result = eval_tests(tmp_path)
        assert result["name"] == "tests"
        # 12 + 8 = 20 passed, 0 + 2 = 2 failed
        assert result["score"] == round(20 / 22, 4)
        assert result["passed"] is False

    def test_single_suite_still_works(self, tmp_path):
        output = "Tests: 5 passed, 0 failed\n"
        (tmp_path / "package.json").write_text('{"name": "app"}\n')
        with patch("factory.eval.hygiene._run_cmd", return_value=(0, output, "")):
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True


class TestJavaMavenMultiModuleAggregation:
    """Multi-module Maven builds report multiple 'Tests run:' lines — all must be summed."""

    MULTI_MODULE_OUTPUT = (
        "[INFO] --- maven-surefire-plugin:3.0.0:test (default-test) @ module-a ---\n"
        "Tests run: 10, Failures: 1, Errors: 0, Skipped: 0\n"
        "[INFO] --- maven-surefire-plugin:3.0.0:test (default-test) @ module-b ---\n"
        "Tests run: 20, Failures: 0, Errors: 2, Skipped: 0\n"
    )

    def test_aggregates_multiple_modules(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        with (
            patch("factory.eval.hygiene._run_cmd", return_value=(1, self.MULTI_MODULE_OUTPUT, "")),
            patch("factory.eval.hygiene._java_build_tool", return_value=["mvn"]),
        ):
            result = eval_tests(tmp_path)
        assert result["name"] == "tests"
        # module-a: 10 - 1 - 0 = 9 passed, 1 failed
        # module-b: 20 - 0 - 2 = 18 passed, 2 failed
        # total: 27 passed, 3 failed
        assert result["score"] == round(27 / 30, 4)
        assert result["passed"] is False
        assert "27 passed" in result["details"]
        assert "3 failed" in result["details"]

    def test_single_module_still_works(self, tmp_path):
        output = "Tests run: 5, Failures: 0, Errors: 0, Skipped: 0\n"
        (tmp_path / "pom.xml").write_text("<project></project>")
        with (
            patch("factory.eval.hygiene._run_cmd", return_value=(0, output, "")),
            patch("factory.eval.hygiene._java_build_tool", return_value=["mvn"]),
        ):
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True


class TestRustWorkspaceDedup:
    """Bug 1: Rust workspace triple-counting — member crates should be deduplicated."""

    def test_rust_workspace_deduplicates_members(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers = ['crate_a', 'crate_b']\n")
        (tmp_path / "crate_a").mkdir()
        (tmp_path / "crate_a" / "Cargo.toml").write_text("[package]\nname = 'crate_a'\n")
        (tmp_path / "crate_b").mkdir()
        (tmp_path / "crate_b" / "Cargo.toml").write_text("[package]\nname = 'crate_b'\n")
        roots = _find_sub_projects(tmp_path)
        assert len(roots) == 1
        assert roots[0] == tmp_path


class TestJavaProjectDetected:
    """Bug 2: Java missing from hygiene eval — pom.xml should be detected."""

    def test_java_project_detected(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        roots = _find_sub_projects(tmp_path)
        assert tmp_path in roots

    def test_java_gradle_detected(self, tmp_path):
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'")
        assert _detect_java_project(tmp_path) is True

    def test_java_gradle_kts_detected(self, tmp_path):
        (tmp_path / "build.gradle.kts").write_text("plugins { java }")
        assert _detect_java_project(tmp_path) is True

    def test_non_java_not_detected(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        assert _detect_java_project(tmp_path) is False


class TestJestDoesNotDoubleCount:
    """Bug 3: Jest over-counting — regex should match Tests: line only, not Test Suites:."""

    def test_jest_does_not_double_count_suites(self, tmp_path):
        output = (
            "Test Suites: 2 passed, 2 total\n"
            "Tests:       10 passed, 10 total\n"
            "Snapshots:   0 total\n"
            "Time:        1.5 s\n"
        )
        (tmp_path / "package.json").write_text('{"name": "app"}\n')
        with patch("factory.eval.hygiene._run_cmd", return_value=(0, output, "")):
            result = eval_tests(tmp_path)
        assert result["score"] == 1.0
        assert "10 passed" in result["details"]

    def test_jest_with_failures_counts_correctly(self, tmp_path):
        output = (
            "Test Suites: 1 failed, 2 passed, 3 total\n"
            "Tests:       2 failed, 8 passed, 10 total\n"
        )
        (tmp_path / "package.json").write_text('{"name": "app"}\n')
        with patch("factory.eval.hygiene._run_cmd", return_value=(1, output, "")):
            result = eval_tests(tmp_path)
        assert result["score"] == round(8 / 10, 4)
        assert result["passed"] is False
