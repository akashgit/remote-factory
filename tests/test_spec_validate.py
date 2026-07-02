"""Tests for factory.spec.validate — agent-based spec validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.spec.validate import ValidationResult, validate_spec


def _write_spec(project: Path, spec_content: str) -> Path:
    """Write a GRAPH-SPEC.md at the project root."""
    spec_path = project / "GRAPH-SPEC.md"
    spec_path.write_text(spec_content)
    return spec_path


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
"""


def _mock_agent_clean() -> AsyncMock:
    """Return a mock invoke_agent that reports no issues."""
    data = json.dumps({"errors": [], "warnings": []})
    return AsyncMock(return_value=(data, 0))


def _mock_agent_with_errors() -> AsyncMock:
    """Return a mock invoke_agent that reports errors and warnings."""
    data = json.dumps(
        {
            "errors": ["Module 'cli': path 'factory/cli.py' does not exist"],
            "warnings": ["Orphan module: 'utils' has zero consumers"],
        }
    )
    return AsyncMock(return_value=(data, 0))


def _mock_agent_failure() -> AsyncMock:
    """Return a mock invoke_agent that fails."""
    return AsyncMock(return_value=("error occurred", 1))


def _mock_agent_bad_json() -> AsyncMock:
    """Return a mock invoke_agent that returns unparseable output."""
    return AsyncMock(return_value=("This is not JSON at all", 0))


class TestValidateSpec:
    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_clean)
    async def test_clean_validation(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.passed
        assert result.errors == []
        assert result.warnings == []

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_with_errors)
    async def test_validation_with_errors(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert not result.passed
        assert len(result.errors) == 1
        assert "does not exist" in result.errors[0]
        assert len(result.warnings) == 1
        assert "Orphan" in result.warnings[0]

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_failure)
    async def test_agent_failure_produces_warning(
        self, mock_agent: AsyncMock, tmp_path: Path
    ) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.passed
        assert any("failed" in w for w in result.warnings)

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_bad_json)
    async def test_bad_json_produces_warning(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.passed
        assert any("Could not parse" in w for w in result.warnings)

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_clean)
    async def test_haiku_model_used(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        await validate_spec(tmp_path)
        mock_agent.assert_awaited_once()
        assert mock_agent.call_args.kwargs.get("model") == "haiku"

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_clean)
    async def test_report_written(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        await validate_spec(tmp_path)
        report_path = tmp_path / ".factory" / "spec_validation.md"
        assert report_path.is_file()
        report = report_path.read_text()
        assert "## Summary" in report
        assert "PASS" in report

    @patch("factory.agents.runner.invoke_agent", new_callable=_mock_agent_with_errors)
    async def test_fail_report(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        await validate_spec(tmp_path)
        report = (tmp_path / ".factory" / "spec_validation.md").read_text()
        assert "FAIL" in report
        assert "## Errors" in report

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(
            return_value=(json.dumps({"errors": "single error string", "warnings": "warn"}), 0)
        ),
    )
    async def test_string_errors_wrapped_in_list(
        self, mock_agent: AsyncMock, tmp_path: Path
    ) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.errors == ["single error string"]
        assert result.warnings == ["warn"]

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(
            return_value=(json.dumps([{"errors": ["wrapped"], "warnings": []}]), 0)
        ),
    )
    async def test_list_wrapped_dict_unwrapped(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.errors == ["wrapped"]

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(json.dumps([{"a": 1}, {"b": 2}]), 0)),
    )
    async def test_multi_element_list_produces_warning(
        self, mock_agent: AsyncMock, tmp_path: Path
    ) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.passed
        assert any("Could not parse" in w for w in result.warnings)


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
