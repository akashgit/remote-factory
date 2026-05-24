"""Tests for eval_spec item classification and auto-promotion."""

import pytest

from factory.discovery.eval_spec import (
    classify_eval_spec_item,
    generate_project_eval_from_spec,
)


class TestClassifyEvalSpecItem:
    @pytest.mark.parametrize("item", [
        "Run the CLI with --help and verify it prints usage info",
        "Start the dev server and confirm the landing page loads",
        "Build and start Docker containers and verify services are healthy",
        "Import the package in a Python shell and verify no import errors",
        "Execute the test suite and check all tests pass",
        "Deploy the service to staging and verify health endpoint",
        "Launch the application and verify it starts without errors",
        "Install dependencies and verify build succeeds",
    ])
    def test_executable_verb_items(self, item):
        assert classify_eval_spec_item(item) == "executable"

    @pytest.mark.parametrize("item", [
        "Verify the code follows clean architecture principles",
        "Check that error messages are user-friendly",
        "Ensure the documentation is up to date",
        "Confirm the API design follows REST best practices",
        "The UI should be responsive on mobile devices",
    ])
    def test_judgmental_items(self, item):
        assert classify_eval_spec_item(item) == "judgmental"

    def test_backtick_command_is_executable(self):
        assert classify_eval_spec_item("Verify `pytest -v` runs cleanly") == "executable"

    def test_flag_pattern_is_executable(self):
        assert classify_eval_spec_item("Check the --verbose flag works") == "executable"

    def test_tool_name_is_executable(self):
        assert classify_eval_spec_item("Verify python can import the module") == "executable"

    def test_empty_string_is_judgmental(self):
        assert classify_eval_spec_item("") == "judgmental"

    def test_whitespace_only_is_judgmental(self):
        assert classify_eval_spec_item("   ") == "judgmental"


class TestGenerateProjectEvalFromSpec:
    def test_help_flag_promotion(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\nversion = "0.1.0"\n\n'
            '[project.scripts]\nmyapp = "myapp.cli:main"\n'
        )
        spec = ["Run the CLI with --help and verify it prints usage info"]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 1
        assert dims[0].command == "myapp --help"
        assert dims[0].parse == "exit_code"
        assert dims[0].name.startswith("spec_")

    def test_import_promotion(self, tmp_path):
        pkg_dir = tmp_path / "mypackage"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        spec = ["Import the package in a Python shell and verify no import errors"]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 1
        assert 'import mypackage' in dims[0].command

    def test_docker_promotion(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
        spec = ["Build and start Docker containers and verify services are healthy"]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 1
        assert "docker compose build" in dims[0].command

    def test_judgmental_items_skipped(self, tmp_path):
        spec = [
            "Verify the code follows clean architecture principles",
            "Ensure documentation is comprehensive",
        ]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 0

    def test_backtick_command_extraction(self, tmp_path):
        spec = ["Run `echo hello` and verify output"]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 1
        assert dims[0].command == "echo hello"

    def test_no_entry_point_skips_help(self, tmp_path):
        spec = ["Run the CLI with --help and verify it prints usage info"]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 0

    def test_mixed_spec_items(self, tmp_path):
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        spec = [
            "Import the package in a Python shell and verify no errors",
            "Verify the API design is RESTful",
            "Run `echo test` to verify shell works",
        ]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 2

    def test_empty_spec_returns_empty(self, tmp_path):
        dims = generate_project_eval_from_spec([], tmp_path)
        assert dims == []

    def test_manage_py_promotion(self, tmp_path):
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
        spec = ["Run python manage.py check and verify no issues reported"]
        dims = generate_project_eval_from_spec(spec, tmp_path)
        assert len(dims) == 1
        assert dims[0].command == "python manage.py check"
