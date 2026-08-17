"""Tests for LLMNode tool execution."""
from __future__ import annotations

import asyncio

import pytest

from factory.workflow.llm_tools import (
    BASH_TOOL,
    FILE_EDIT_TOOL,
    FILE_READ_TOOL,
    execute_tool,
)


@pytest.fixture
def work_dir(tmp_path):
    (tmp_path / "test.py").write_text("line1\nline2\nline3\n")
    return tmp_path


class TestBashTool:
    def test_bash_tool_definition(self):
        assert BASH_TOOL.name == "bash"
        assert BASH_TOOL.executor == "bash"
        assert "command" in BASH_TOOL.input_schema["properties"]

    def test_execute_bash(self, work_dir):
        result = asyncio.run(
            execute_tool("bash", {"command": "echo hello"}, BASH_TOOL, work_dir)
        )
        assert "hello" in result

    def test_execute_bash_with_returncode(self, work_dir):
        result = asyncio.run(
            execute_tool("bash", {"command": "exit 1"}, BASH_TOOL, work_dir)
        )
        assert "exit code: 1" in result

    def test_execute_bash_timeout(self, work_dir):
        result = asyncio.run(
            execute_tool(
                "bash", {"command": "sleep 10"}, BASH_TOOL, work_dir,
                cmd_timeout=1,
            )
        )
        assert "timed out" in result


class TestFileReadTool:
    def test_read_existing(self, work_dir):
        result = asyncio.run(
            execute_tool("file_read", {"path": "test.py"}, FILE_READ_TOOL, work_dir)
        )
        assert "line1" in result

    def test_read_missing(self, work_dir):
        result = asyncio.run(
            execute_tool("file_read", {"path": "nope.py"}, FILE_READ_TOOL, work_dir)
        )
        assert "not found" in result.lower()


class TestFileEditTool:
    def test_edit_existing(self, work_dir):
        result = asyncio.run(
            execute_tool(
                "file_edit",
                {"path": "test.py", "old_string": "line2", "new_string": "modified"},
                FILE_EDIT_TOOL,
                work_dir,
            )
        )
        assert "Edited" in result
        assert "modified" in (work_dir / "test.py").read_text()

    def test_edit_missing_string(self, work_dir):
        result = asyncio.run(
            execute_tool(
                "file_edit",
                {"path": "test.py", "old_string": "nonexistent", "new_string": "x"},
                FILE_EDIT_TOOL,
                work_dir,
            )
        )
        assert "not found" in result.lower()
