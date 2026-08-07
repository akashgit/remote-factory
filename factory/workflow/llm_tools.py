"""Tool execution dispatch for LLMNode tool-use loops."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from factory.workflow.primitives import ToolDef

log = structlog.get_logger()

BASH_TOOL = ToolDef(
    name="bash",
    description="Execute a bash command. Returns stdout and stderr combined.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to run",
            },
        },
        "required": ["command"],
    },
    executor="bash",
)

FILE_READ_TOOL = ToolDef(
    name="file_read",
    description="Read a file's contents.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    executor="file_read",
)

FILE_EDIT_TOOL = ToolDef(
    name="file_edit",
    description="Replace a string in a file.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    executor="file_edit",
)

_MAX_OUTPUT = 100_000


async def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_def: ToolDef,
    cwd: Path,
    *,
    cmd_timeout: int = 300,
) -> str:
    executor = tool_def.executor
    if executor == "bash":
        return await _exec_bash(tool_input.get("command", ""), cwd, cmd_timeout)
    if executor == "file_read":
        return _exec_file_read(tool_input.get("path", ""), cwd)
    if executor == "file_write":
        return _exec_file_write(
            tool_input.get("path", ""),
            tool_input.get("content", ""),
            cwd,
        )
    if executor == "file_edit":
        return _exec_file_edit(
            tool_input.get("path", ""),
            tool_input.get("old_string", ""),
            tool_input.get("new_string", ""),
            cwd,
        )
    return f"Unknown executor: {executor}"


async def _exec_bash(command: str, cwd: Path, timeout: int) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace") if stdout else ""
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
    except asyncio.TimeoutError:
        proc.kill()
        output = f"ERROR: command timed out after {timeout}s"
    except Exception as e:
        output = f"ERROR: {e}"

    if len(output) > _MAX_OUTPUT:
        half = _MAX_OUTPUT // 2
        output = output[:half] + f"\n\n... [{len(output) - _MAX_OUTPUT} chars truncated] ...\n\n" + output[-half:]
    return output


def _exec_file_read(path: str, cwd: Path) -> str:
    target = (cwd / path).resolve()
    if not target.exists():
        return f"File not found: {path}"
    text = target.read_text(errors="replace")
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + f"\n... [{len(text) - _MAX_OUTPUT} chars truncated]"
    return text


def _exec_file_write(path: str, content: str, cwd: Path) -> str:
    target = (cwd / path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"Wrote {len(content)} bytes to {path}"


def _exec_file_edit(path: str, old: str, new: str, cwd: Path) -> str:
    target = (cwd / path).resolve()
    if not target.exists():
        return f"File not found: {path}"
    text = target.read_text()
    if old not in text:
        return f"old_string not found in {path}"
    text = text.replace(old, new, 1)
    target.write_text(text)
    return f"Edited {path}"
