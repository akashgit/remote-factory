"""ClaudeRunner — Claude Code CLI backend implementation (v2)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.cli_adapter import CLIAdapter
from factory.runners.types import (
    AgentStep,
    ExecutionTrace,
    FileLocation,
    PermissionMode,
    RunnerCapability,
    RunnerRequest,
    RunnerResponse,
    SandboxMode,
    ToolCallStatus,
    ToolCallTrace,
    ToolKind,
    UsageStats,
)

if TYPE_CHECKING:
    from factory.models import AgentUsage

logger = logging.getLogger(__name__)

# -- Tool name → ToolKind mapping ------------------------------------------

_TOOL_KIND_MAP: dict[str, ToolKind] = {
    "Read": ToolKind.READ,
    "Edit": ToolKind.EDIT,
    "Write": ToolKind.EDIT,
    "MultiEdit": ToolKind.EDIT,
    "NotebookEdit": ToolKind.EDIT,
    "Bash": ToolKind.EXECUTE,
    "Grep": ToolKind.SEARCH,
    "Glob": ToolKind.SEARCH,
    "WebFetch": ToolKind.FETCH,
    "WebSearch": ToolKind.FETCH,
    "Agent": ToolKind.OTHER,
    "TodoWrite": ToolKind.OTHER,
    "Task": ToolKind.OTHER,
}


def _tool_kind(name: str) -> ToolKind:
    return _TOOL_KIND_MAP.get(name, ToolKind.OTHER)


# -- Stream-JSON (JSONL) parser -------------------------------------------

def _parse_stream_json(
    jsonl_text: str,
) -> tuple[str, ExecutionTrace, UsageStats | None, str | None]:
    """Parse Claude Code stream-json JSONL output.

    Each line is a JSON object with a 'type' field:
    - 'system': init event (contains session info)
    - 'assistant': text or tool_use content blocks
    - 'user': tool_result content blocks
    - 'result': final summary with usage stats

    Returns (final_text, trace, usage, session_id).
    """
    trace = ExecutionTrace()
    final_text = ""
    usage: UsageStats | None = None
    session_id: str | None = None
    current_step_index = 0
    current_step = AgentStep(step_index=0)

    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "system":
            session_id = event.get("session_id")

        elif event_type == "assistant":
            # Start a new step for each assistant turn
            current_step = AgentStep(step_index=current_step_index)
            current_step_index += 1

            text_parts: list[str] = []
            for block in event.get("message", {}).get("content", []):
                block_type = block.get("type", "")

                if block_type == "text":
                    text_parts.append(block.get("text", ""))

                elif block_type == "thinking":
                    thinking = block.get("thinking", "")
                    if thinking:
                        trace.thinking_blocks.append(thinking)

                elif block_type == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    kind = _tool_kind(tool_name)

                    input_summary = json.dumps(tool_input)[:200] if tool_input else None

                    tc = ToolCallTrace(
                        tool_name=tool_name,
                        kind=kind,
                        status=ToolCallStatus.COMPLETED,
                        input_summary=input_summary,
                    )

                    # Extract file locations from tool inputs
                    if "file_path" in tool_input:
                        tc.locations.append(
                            FileLocation(
                                path=tool_input["file_path"],
                                line=tool_input.get("line"),
                            )
                        )
                    if "path" in tool_input and isinstance(tool_input["path"], str):
                        tc.locations.append(FileLocation(path=tool_input["path"]))

                    # Track files read/written/commands
                    if kind == ToolKind.READ:
                        path = tool_input.get("file_path", "")
                        if path and path not in trace.files_read:
                            trace.files_read.append(path)
                    elif kind == ToolKind.EDIT:
                        path = tool_input.get("file_path", "")
                        if path and path not in trace.files_written:
                            trace.files_written.append(path)
                    elif kind == ToolKind.EXECUTE:
                        cmd = tool_input.get("command", "")
                        if cmd:
                            trace.commands_executed.append(cmd[:200])

                    current_step.tool_calls.append(tc)

            if text_parts:
                current_step.output_text = "\n".join(text_parts)

            trace.steps.append(current_step)

        elif event_type == "user":
            # Tool results — update the most recent tool calls with output
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_result":
                    output = block.get("content", "")
                    if isinstance(output, list):
                        output = " ".join(
                            b.get("text", "") for b in output if b.get("type") == "text"
                        )
                    is_error = block.get("is_error", False)

                    # Find the matching tool call by tool_use_id
                    tool_use_id = block.get("tool_use_id", "")
                    if tool_use_id and current_step.tool_calls:
                        for tc in current_step.tool_calls:
                            if not tc.output_summary:
                                tc.output_summary = str(output)[:200] if output else None
                                if is_error:
                                    tc.status = ToolCallStatus.FAILED
                                    tc.error = str(output)[:200] if output else "unknown error"
                                break

        elif event_type == "result":
            # Final summary — extract text and usage
            result_text = event.get("result", "")
            if isinstance(result_text, str):
                final_text = result_text

            usage_block = event.get("usage", {})
            if usage_block:
                usage = UsageStats(
                    input_tokens=usage_block.get("input_tokens"),
                    output_tokens=usage_block.get("output_tokens"),
                    total_tokens=(
                        (usage_block.get("input_tokens") or 0)
                        + (usage_block.get("output_tokens") or 0)
                    ) or None,
                    cost_usd=event.get("cost_usd"),
                    duration_seconds=(
                        (event.get("duration_ms") or 0) / 1000.0
                    ) or None,
                    model_used=event.get("model"),
                )

            session_id = session_id or event.get("session_id")

    return final_text, trace, usage, session_id


# -- Backward-compat usage parser (v1) ------------------------------------

def _parse_usage(data: dict) -> "AgentUsage":
    """Extract AgentUsage from Claude Code JSON output (v1 compat)."""
    from factory.models import AgentUsage

    usage_block = data.get("usage", {})
    return AgentUsage(
        input_tokens=usage_block.get("input_tokens", 0),
        output_tokens=usage_block.get("output_tokens", 0),
        cache_read_tokens=usage_block.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage_block.get("cache_creation_input_tokens", 0),
        total_cost_usd=data.get("cost_usd", 0.0) or 0.0,
        duration_ms=data.get("duration_ms", 0.0) or 0.0,
        num_turns=data.get("num_turns", 0) or 0,
        model=data.get("model", ""),
    )


# -- ClaudeRunner (v2) ----------------------------------------------------

class ClaudeRunner(CLIAdapter):
    """Runner implementation for Claude Code CLI."""

    name: str = "claude"

    def __init__(self) -> None:
        super().__init__(
            name="claude",
            display_name="Claude Code",
            capabilities={
                RunnerCapability.MODEL_OVERRIDE,
                RunnerCapability.SESSION_RESUME,
                RunnerCapability.STRUCTURED_OUTPUT,
                RunnerCapability.STREAMING,
                RunnerCapability.INTERACTIVE,
                RunnerCapability.EXECUTION_TRACE,
                RunnerCapability.TOOL_CONTROL,
                RunnerCapability.MAX_TURNS,
            },
            binary="claude",
        )

    def _inject_prompt_proxy(self, request: RunnerRequest) -> str:
        """Claude handles most features natively — only proxy what it can't do."""
        parts: list[str] = []

        # max_tokens: no native flag — prompt proxy
        if request.max_tokens is not None:
            parts.append(
                f"IMPORTANT: Keep your total output under {request.max_tokens} tokens. Be concise."
            )

        # max_cost_usd: no native flag — prompt proxy
        if request.max_cost_usd is not None:
            parts.append(
                f"IMPORTANT: This invocation has a budget of ${request.max_cost_usd:.2f}. "
                "Minimize token usage. Avoid reading large files unnecessarily."
            )

        # sandbox_mode: no native flag — proxy via tool restrictions
        if request.sandbox_mode == SandboxMode.READ_ONLY:
            parts.append(
                "IMPORTANT: READ-ONLY MODE. Do not write, edit, or delete any files. "
                "Do not execute commands that modify the filesystem."
            )

        return "\n\n".join(parts)

    def _build_command(
        self,
        request: RunnerRequest,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        cmd = ["claude"]
        if prompt_file:
            cmd.extend(["--append-system-prompt-file", prompt_file])
        cmd.extend(["-p", request.task, "--output-format", "stream-json", "--verbose"])

        # Permission mode: skip_permissions=False overrides permission_mode for backward compat
        if request.skip_permissions and request.permission_mode == PermissionMode.AUTO:
            cmd.append("--dangerously-skip-permissions")
        elif not request.skip_permissions:
            pass  # Explicit opt-out — don't add the flag
        elif request.permission_mode == PermissionMode.AUTO:
            cmd.append("--dangerously-skip-permissions")

        # Tool control (native)
        if request.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(request.allowed_tools)])
        if request.disallowed_tools:
            cmd.extend(["--disallowedTools", ",".join(request.disallowed_tools)])

        # Resource limits
        if request.max_turns is not None:
            cmd.extend(["--max-turns", str(request.max_turns)])

        # Model override
        if request.model:
            cmd.extend(["--model", request.model])

        # Session resume
        if request.session_name:
            cmd.extend(["--name", request.session_name])

        return cmd

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> RunnerResponse:
        # Try stream-json JSONL parsing first
        final_text, trace, usage, session_id = _parse_stream_json(stdout)

        if final_text or trace.steps:
            return RunnerResponse(
                output=final_text or stdout,
                exit_code=exit_code,
                usage=usage,
                trace=trace,
                session_id=session_id,
            )

        # Fallback: try single JSON object (legacy --output-format json)
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                result_value = data.get("result", stdout)
                result_text = result_value if isinstance(result_value, str) else stdout
                usage_block = data.get("usage", {})
                fallback_usage = None
                if usage_block:
                    fallback_usage = UsageStats(
                        input_tokens=usage_block.get("input_tokens"),
                        output_tokens=usage_block.get("output_tokens"),
                        cost_usd=data.get("cost_usd"),
                        duration_seconds=(
                            (data.get("duration_ms") or 0) / 1000.0
                        ) or None,
                        model_used=data.get("model"),
                    )
                return RunnerResponse(
                    output=result_text,
                    exit_code=exit_code,
                    usage=fallback_usage,
                    session_id=data.get("session_id"),
                )
        except (json.JSONDecodeError, ValueError):
            pass

        return RunnerResponse(output=stdout, exit_code=exit_code)

    def _build_env(self, request: RunnerRequest) -> dict[str, str]:
        env = super()._build_env(request)
        if request.model:
            env["FACTORY_MODEL"] = request.model
        return env

    # -- v1 backward-compat methods ----------------------------------------

    async def headless(  # type: ignore[override]
        self,
        prompt: str | RunnerRequest = "",
        task: str = "",
        cwd: Path | str = ".",
        *,
        timeout: float = 600.0,
        model: str | None = None,
        dangerously_skip_permissions: bool = True,
        role: str = "unknown",
        session_name: str | None = None,
        tmux_persist: bool = False,
    ) -> tuple[str, int, "AgentUsage | None"] | RunnerResponse:
        """Run a headless Claude Code invocation.

        Supports both v1 (positional args → tuple) and v2 (RunnerRequest → RunnerResponse).
        """
        if isinstance(prompt, RunnerRequest):
            return await CLIAdapter.headless(self, prompt)

        # v1 path: build from positional args
        if tmux_persist:
            from factory.runners._tmux_persist import find_project_path, run_in_tmux, tmux_available

            if tmux_available():
                return await run_in_tmux(
                    prompt, task, Path(cwd), role, find_project_path(Path(cwd)),
                    model=model,
                    dangerously_skip_permissions=dangerously_skip_permissions,
                )
            logger.warning("tmux not available; falling back to headless")

        # For v1 compat: write system prompt to file, use task as -p arg
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(prompt)
            prompt_file.close()

            cmd = [
                "claude", "--append-system-prompt-file", prompt_file.name,
                "-p", task,
                "--output-format", "stream-json", "--verbose",
            ]
            if dangerously_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            if model:
                cmd.extend(["--model", model])
            if session_name:
                cmd.extend(["--name", session_name])

            logger.info("ClaudeRunner headless: cwd=%s, model=%s", cwd, model)

            env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
            if model:
                env["FACTORY_MODEL"] = model

            stream = should_stream()
            prefix_str = f"[claude:{role}]" if stream else None

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    stream_subprocess(proc, stream=stream, prefix=prefix_str),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()  # type: ignore[union-attr]
                await proc.wait()  # type: ignore[union-attr]
                logger.error("ClaudeRunner timed out after %ss", timeout)
                return f"Agent timed out after {timeout}s", 1, None
            except FileNotFoundError:
                logger.error("'claude' CLI not found on PATH")
                return "Error: 'claude' CLI not found on PATH", 1, None

            raw_stdout = stdout_bytes.decode()
            stderr_str = stderr_bytes.decode()
            return_code = proc.returncode or 0

            if return_code != 0:
                logger.warning("ClaudeRunner exited with code %d: %s", return_code, stderr_str[:200])

            # Try stream-json parsing → extract text and v1 usage
            final_text, _trace, v2_usage, _sid = _parse_stream_json(raw_stdout)
            if final_text or _trace.steps:
                v1_usage = _v2_to_v1_usage(v2_usage, raw_stdout) if v2_usage else None
                return final_text or raw_stdout, return_code, v1_usage

            # Fallback: try single JSON (legacy --output-format json)
            usage = None
            result_text = raw_stdout
            try:
                data = json.loads(raw_stdout)
                if isinstance(data, dict):
                    result_value = data.get("result", raw_stdout)
                    result_text = result_value if isinstance(result_value, str) else raw_stdout
                    usage = _parse_usage(data)
            except (json.JSONDecodeError, ValueError):
                logger.debug("Could not parse JSON output, returning raw stdout")

            return result_text, return_code, usage
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)

    def interactive_run(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        model: str | None = None,
        role: str = "ceo",
        dangerously_skip_permissions: bool = False,
        session_name: str | None = None,
    ) -> int:
        """Run an interactive Claude Code session (v1 compat).

        Returns the exit code so the caller can clean up in a finally block.
        """
        _ = role
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(prompt)
            prompt_file.close()

            cmd = [
                "claude",
                "--append-system-prompt-file", prompt_file.name,
            ]
            if dangerously_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            cmd.append(task)
            if model:
                cmd.extend(["--model", model])
                os.environ["FACTORY_MODEL"] = model
            if session_name:
                cmd.extend(["--name", session_name])

            logger.info("ClaudeRunner interactive_run: cwd=%s", cwd)

            result = subprocess.run(cmd, cwd=cwd)
            return result.returncode
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)


def _v2_to_v1_usage(v2_usage: UsageStats, raw_stdout: str) -> "AgentUsage | None":
    """Convert v2 UsageStats to v1 AgentUsage for backward compat."""
    from factory.models import AgentUsage

    # Try to extract additional fields from raw JSONL result event
    num_turns = 0
    cache_read = 0
    cache_creation = 0
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            num_turns = event.get("num_turns", 0) or 0
            ub = event.get("usage", {})
            cache_read = ub.get("cache_read_input_tokens", 0)
            cache_creation = ub.get("cache_creation_input_tokens", 0)
            break

    return AgentUsage(
        input_tokens=v2_usage.input_tokens or 0,
        output_tokens=v2_usage.output_tokens or 0,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        total_cost_usd=v2_usage.cost_usd or 0.0,
        duration_ms=(v2_usage.duration_seconds or 0.0) * 1000.0,
        num_turns=num_turns,
        model=v2_usage.model_used or "",
    )
