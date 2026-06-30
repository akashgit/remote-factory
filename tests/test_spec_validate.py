"""Tests for factory.spec.validate — path checks, Haiku import verification, orphan/hub, coupling."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.spec.validate import (
    ValidationResult,
    validate_spec,
)


def _write_spec(project: Path, spec_content: str) -> Path:
    """Write a GRAPH-SPEC.md at the project root."""
    spec_path = project / "GRAPH-SPEC.md"
    spec_path.write_text(spec_content)
    return spec_path


def _make_python_project(tmp_path: Path) -> Path:
    """Create a fixture Python project with known dependency structure."""
    project = tmp_path / "myproject"
    project.mkdir()

    models = project / "myapp" / "models.py"
    models.parent.mkdir(parents=True)
    (project / "myapp" / "__init__.py").write_text("")
    models.write_text("class User:\n    pass\n\nclass Config:\n    pass\n")

    store = project / "myapp" / "store.py"
    store.write_text("from myapp import models\n\nclass Store:\n    pass\n")

    api = project / "myapp" / "api.py"
    api.write_text("from myapp import models\nfrom myapp import store\n")

    utils = project / "myapp" / "utils.py"
    utils.write_text("def slugify(s: str) -> str:\n    return s.lower()\n")

    return project


BASIC_SPEC = """\
# Repo Spec

## Modules

### models
- **Path:** myapp/models.py
- **Role:** Data models
- **Exports:** User, Config
- **Depends on:** none

### store
- **Path:** myapp/store.py
- **Role:** Data persistence
- **Exports:** Store
- **Depends on:** models

### api
- **Path:** myapp/api.py
- **Role:** API layer
- **Exports:** app
- **Depends on:** models, store

### utils
- **Path:** myapp/utils.py
- **Role:** Utility functions
- **Exports:** slugify
- **Depends on:** none

## Dependency Edges

| Source | Target | Import Type | Coupling |
|--------|--------|-------------|----------|
| store | models | direct | strong |
| api | models | direct | strong |
| api | store | direct | weak |
"""


def _mock_haiku_no_findings() -> AsyncMock:
    """Return a mock invoke_agent that reports no import issues."""
    return AsyncMock(return_value=("[]", 0))


def _mock_haiku_with_phantom() -> AsyncMock:
    """Return a mock invoke_agent that reports a phantom edge."""
    findings = json.dumps(
        [
            {
                "type": "phantom",
                "source": "utils",
                "target": "models",
                "detail": "utils declares dependency on models but no import found",
            }
        ]
    )
    return AsyncMock(return_value=(findings, 0))


def _mock_haiku_failure() -> AsyncMock:
    """Return a mock invoke_agent that fails."""
    return AsyncMock(return_value=("error occurred", 1))


class TestPathChecks:
    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_valid_paths_no_errors(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        path_errors = [e for e in result.errors if "does not exist" in e]
        assert path_errors == []

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_missing_path_produces_error(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        spec_with_bad_path = BASIC_SPEC.replace("myapp/utils.py", "myapp/nonexistent.py")
        _write_spec(project, spec_with_bad_path)
        result = await validate_spec(project)
        path_errors = [e for e in result.errors if "does not exist" in e]
        assert len(path_errors) == 1
        assert "utils" in path_errors[0]

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_missing_path_fails_validation(
        self, mock_agent: AsyncMock, tmp_path: Path
    ) -> None:
        project = _make_python_project(tmp_path)
        spec_with_bad_path = BASIC_SPEC.replace("myapp/utils.py", "myapp/nonexistent.py")
        _write_spec(project, spec_with_bad_path)
        result = await validate_spec(project)
        assert not result.passed


class TestImportCrossReference:
    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_valid_imports_no_warnings(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        import_warns = [w for w in result.warnings if "Phantom" in w or "Missing" in w]
        assert import_warns == []

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_with_phantom)
    async def test_phantom_dependency_produces_warning(
        self, mock_agent: AsyncMock, tmp_path: Path
    ) -> None:
        project = _make_python_project(tmp_path)
        spec_with_phantom = BASIC_SPEC.replace(
            "### utils\n- **Path:** myapp/utils.py\n- **Role:** Utility functions\n"
            "- **Exports:** slugify\n- **Depends on:** none",
            "### utils\n- **Path:** myapp/utils.py\n- **Role:** Utility functions\n"
            "- **Exports:** slugify\n- **Depends on:** models",
        )
        _write_spec(project, spec_with_phantom)
        result = await validate_spec(project)
        phantom_warns = [w for w in result.warnings if "Phantom" in w]
        assert len(phantom_warns) >= 1
        assert "utils" in phantom_warns[0]

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_haiku_called_with_model(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        mock_agent.assert_awaited_once()
        call_kwargs = mock_agent.call_args
        assert call_kwargs.kwargs.get("model") == "haiku"

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_failure)
    async def test_haiku_failure_produces_warning(
        self, mock_agent: AsyncMock, tmp_path: Path
    ) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        fail_warns = [w for w in result.warnings if "verification failed" in w]
        assert len(fail_warns) == 1


class TestOrphanDetection:
    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_orphan_module_flagged(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        orphan_warns = [w for w in result.warnings if "Orphan module" in w]
        orphan_names = [w for w in orphan_warns if "utils" in w]
        assert len(orphan_names) >= 1

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_consumed_module_not_orphan(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        result = await validate_spec(project)
        orphan_warns = [w for w in result.warnings if "Orphan module" in w]
        orphan_names = [w for w in orphan_warns if "models" in w]
        assert orphan_names == []


class TestValidationReport:
    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_report_written(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        report_path = project / ".factory" / "spec_validation.md"
        assert report_path.is_file()

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_report_contains_summary(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        report = (project / ".factory" / "spec_validation.md").read_text()
        assert "## Summary" in report
        assert "PASS" in report

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_haiku_no_findings)
    async def test_report_no_coupling_table(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        project = _make_python_project(tmp_path)
        _write_spec(project, BASIC_SPEC)
        await validate_spec(project)
        report = (project / ".factory" / "spec_validation.md").read_text()
        assert "## Coupling Metrics" not in report


class TestValidationResult:
    def test_passed_with_no_errors(self) -> None:
        result = ValidationResult(warnings=["some warning"])
        assert result.passed

    def test_failed_with_errors(self) -> None:
        result = ValidationResult(errors=["path missing"])
        assert not result.passed


class TestMissingSpec:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await validate_spec(tmp_path)


class TestSectionCompleteness:
    """Tests for _check_section_completeness — verifies detection of missing spec sections."""

    def _make_complete_spec(self) -> "RepoSpec":  # noqa: F821
        from factory.spec.parser import ProjectIdentity, RepoSpec

        return RepoSpec(
            identity=ProjectIdentity(name="test-project"),
            problem_statement="A test project for validation.",
            goals="Run tests automatically.",
            non_goals="Not a production system.",
            design_philosophy="Keep it simple.",
            configuration_spec="CLI flag > env var > default.",
            security="No trust boundaries.",
            extension_points="Plugin registry at factory/runners/.",
            implementation_checklist="Must pass all tests.",
        )

    def test_complete_spec_returns_no_warnings(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        warnings = _check_section_completeness(spec)
        assert warnings == []

    def test_missing_problem_statement(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.problem_statement = ""
        warnings = _check_section_completeness(spec)
        assert any("§1 Problem Statement" in w for w in warnings)

    def test_missing_goals(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.goals = ""
        warnings = _check_section_completeness(spec)
        assert any("§2.1 Goals" in w for w in warnings)

    def test_missing_non_goals(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.non_goals = ""
        warnings = _check_section_completeness(spec)
        assert any("§2.2 Non-Goals" in w for w in warnings)

    def test_missing_design_philosophy(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.design_philosophy = ""
        warnings = _check_section_completeness(spec)
        assert any("§2.3 Design Philosophy" in w for w in warnings)

    def test_missing_identity_name(self) -> None:
        from factory.spec.parser import ProjectIdentity
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.identity = ProjectIdentity()
        warnings = _check_section_completeness(spec)
        assert any("§3 Project Identity" in w for w in warnings)

    def test_missing_configuration_spec(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.configuration_spec = ""
        warnings = _check_section_completeness(spec)
        assert any("§10 Configuration" in w for w in warnings)

    def test_missing_security(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.security = ""
        warnings = _check_section_completeness(spec)
        assert any("§13 Security" in w for w in warnings)

    def test_missing_extension_points(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.extension_points = ""
        warnings = _check_section_completeness(spec)
        assert any("§15 Extension Points" in w for w in warnings)

    def test_missing_implementation_checklist(self) -> None:
        from factory.spec.validate import _check_section_completeness

        spec = self._make_complete_spec()
        spec.implementation_checklist = ""
        warnings = _check_section_completeness(spec)
        assert any("§16 Implementation Checklist" in w for w in warnings)

    def test_all_sections_missing_returns_all_warnings(self) -> None:
        from factory.spec.parser import RepoSpec
        from factory.spec.validate import _check_section_completeness

        spec = RepoSpec()
        warnings = _check_section_completeness(spec)
        assert len(warnings) == 9
        assert any("§1" in w for w in warnings)
        assert any("§2.1" in w for w in warnings)
        assert any("§2.2" in w for w in warnings)
        assert any("§2.3" in w for w in warnings)
        assert any("§3" in w for w in warnings)
        assert any("§10" in w for w in warnings)
        assert any("§13" in w for w in warnings)
        assert any("§15" in w for w in warnings)
        assert any("§16" in w for w in warnings)
