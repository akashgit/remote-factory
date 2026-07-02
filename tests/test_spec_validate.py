"""Tests for factory.spec.validate — agent-based spec validation (text-only)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.spec.validate import ValidationResult, _parse_verdict, validate_spec


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

PASS_REPORT = """\
# Spec Validation Report

## Errors
None

## Warnings
- Orphan module: 'utils' has zero consumers

Verdict: PASS
"""

FAIL_REPORT = """\
# Spec Validation Report

## Errors
- Module 'cli': path 'factory/cli.py' does not exist

## Warnings
- Orphan module: 'utils' has zero consumers

Verdict: FAIL
"""


class TestParseVerdict:
    def test_pass(self) -> None:
        assert _parse_verdict("some text\nVerdict: PASS\n") is True

    def test_fail(self) -> None:
        assert _parse_verdict("some text\nVerdict: FAIL\n") is False

    def test_missing_defaults_true(self) -> None:
        assert _parse_verdict("no verdict here") is True

    def test_verdict_mid_text(self) -> None:
        assert _parse_verdict("intro\nVerdict: FAIL\nmore text") is False


class TestValidationResult:
    def test_valid_result(self) -> None:
        result = ValidationResult(report="some report", is_valid=True)
        assert result.is_valid
        assert result.report == "some report"

    def test_invalid_result(self) -> None:
        result = ValidationResult(report="fail report", is_valid=False)
        assert not result.is_valid


class TestValidateSpec:
    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(PASS_REPORT, 0)),
    )
    async def test_pass_report(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.is_valid
        assert "Verdict: PASS" in result.report

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(FAIL_REPORT, 0)),
    )
    async def test_fail_report(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert not result.is_valid
        assert "Verdict: FAIL" in result.report

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("error occurred", 1)),
    )
    async def test_agent_failure_returns_valid(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.is_valid
        assert "failed" in result.report

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=("no verdict line here", 0)),
    )
    async def test_no_verdict_defaults_valid(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        result = await validate_spec(tmp_path)
        assert result.is_valid

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(PASS_REPORT, 0)),
    )
    async def test_haiku_model_used(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        await validate_spec(tmp_path)
        mock_agent.assert_awaited_once()
        assert mock_agent.call_args.kwargs.get("model") == "haiku"

    @patch(
        "factory.agents.runner.invoke_agent",
        new_callable=lambda: AsyncMock(return_value=(PASS_REPORT, 0)),
    )
    async def test_report_written(self, mock_agent: AsyncMock, tmp_path: Path) -> None:
        _write_spec(tmp_path, BASIC_SPEC)
        await validate_spec(tmp_path)
        report_path = tmp_path / ".factory" / "spec_validation.md"
        assert report_path.is_file()
        content = report_path.read_text()
        assert "Verdict: PASS" in content


class TestMissingSpec:
    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await validate_spec(tmp_path)
