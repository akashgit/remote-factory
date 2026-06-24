"""ClaudeRunner — Claude Code CLI backend implementation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult, AgentUsage
    from factory.runners.protocol import RunnerMeta

log = structlog.get_logger()


def _make_ceo_message_emitter(project_path: Path) -> Callable[[bytes], None]:
    """Return a callback that emits ceo.message events for assistant JSONL lines."""
    from factory.events import emit_event

    def _on_line(line: bytes) -> None:
        try:
            parsed = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(parsed, dict) or parsed.get("type") != "assistant":
            return
        message = parsed.get("message", "")
        if isinstance(message, str):
            text = message
        elif isinstance(message, dict):
            content = message.get("content", [])
            text = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            return
        if not text:
            return
        emit_event(
            project_path,
            "ceo.message",
            agent="ceo",
            data={"message": text, "message_type": "assistant"},
        )

    return _on_line


def _parse_usage(data: dict) -> AgentUsage:
    """Extract AgentUsage from Claude Code JSON output."""
    from factory.models import AgentUsage

    usage_block = data.get("usage", {})
    return AgentUsage(
        input_tokens=usage_block.get("input_tokens", 0),
        output_tokens=usage_block.get("output_tokens", 0),
        cache_read_tokens=usage_block.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage_block.get("cache_creation_input_tokens", 0),
        total_cost_usd=data.get("total_cost_usd", 0.0) or 0.0,
        duration_ms=data.get("duration_ms", 0.0) or 0.0,
        num_turns=data.get("num_turns", 0) or 0,
        model=data.get("model", ""),
    )


class ClaudeRunner:
    """Runner implementation for Claude Code CLI."""

    name: str = "claude"

    @classmethod
    def metadata(cls) -> RunnerMeta:
        from factory.runners.protocol import RunnerMeta
        return RunnerMeta(
            name="claude",
            display_name="Claude Code",
            binary="claude",
            install_hint="npm install -g @anthropic-ai/claude-code",
            supports_usage_telemetry=True,
            supports_session_name=True,
            supports_background=True,
        )

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the Claude CLI command, env dict, and temp files."""
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        prompt_file.write(request.prompt)
        prompt_file.close()
        prompt_path = Path(prompt_file.name)

        cmd = [
            "claude", "--append-system-prompt-file", prompt_file.name,
            "-p", request.task,
            "--output-format", "stream-json",
            "--verbose",
        ]
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        if request.model:
            env["FACTORY_MODEL"] = request.model

        return cmd, env, [prompt_path]

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Claude Code invocation."""
        from factory.models import AgentRunResult

        background = request.extras.get("background", False)
        if background:
            from factory.runners._tmux_persist import run_in_background

            stdout, rc, usage = await run_in_background(
                request.prompt, request.task, request.cwd, request.role,
                timeout=request.timeout,
                model=request.model,
                dangerously_skip_permissions=request.skip_permissions,
            )
            return AgentRunResult(stdout=stdout, return_code=rc, usage=usage)

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            from factory.runners._tmux_persist import find_project_path, run_in_tmux, tmux_available

            if tmux_available():
                stdout, rc, usage = await run_in_tmux(
                    request.prompt, request.task, request.cwd, request.role,
                    find_project_path(request.cwd),
                    model=request.model,
                    dangerously_skip_permissions=request.skip_permissions,
                )
                return AgentRunResult(stdout=stdout, return_code=rc, usage=usage)
            log.warning("tmux_not_available")

        cmd, env, temp_files = self.build_command(request)
        try:
            log.info("claude_headless", cwd=str(request.cwd), model=request.model)

            on_line = None
            if request.role == "ceo" and request.project_path is not None:
                on_line = _make_ceo_message_emitter(request.project_path)

            result = await run_subprocess(
                cmd, cwd=str(request.cwd), env=env,
                timeout=request.timeout, runner_name="claude", role=request.role,
                on_line=on_line,
            )

            usage = None
            result_text = result.stdout
            metadata: dict[str, object] = {**result.metadata}

            data: dict[str, object] | None = None
            for line in reversed(result.stdout.strip().splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict) and "result" in parsed:
                    data = parsed
                    break

            if data is not None:
                result_value = data.get("result", result.stdout)
                result_text = result_value if isinstance(result_value, str) else result.stdout
                usage = _parse_usage(data)
                for key in ("session_id", "uuid", "stop_reason", "terminal_reason",
                            "duration_api_ms", "ttft_ms", "is_error", "subtype"):
                    metadata[key] = data.get(key)
                metadata["model_usage"] = data.get("modelUsage")
                metadata["permission_denials"] = data.get("permission_denials")

            return AgentRunResult(
                stdout=result_text,
                return_code=result.return_code,
                usage=usage,
                metadata=metadata,
            )
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def build_interactive_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the CLI command, env dict, and temp files for an interactive invocation."""
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        prompt_file.write(request.prompt)
        prompt_file.close()
        prompt_path = Path(prompt_file.name)

        cmd = [
            "claude",
            "--append-system-prompt-file", prompt_file.name,
        ]
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.append(request.task)
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        if request.model:
            env["FACTORY_MODEL"] = request.model

        return cmd, env, [prompt_path]

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Claude Code session as a subprocess."""
        cmd, env, temp_files = self.build_interactive_command(request)
        try:
            log.info("claude_interactive", cwd=str(request.cwd))
            result = subprocess.run(cmd, cwd=request.cwd, env=env)
            return result.returncode
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)
