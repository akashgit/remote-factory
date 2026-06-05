"""ClaudeRunner — Claude Code CLI backend implementation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult, AgentUsage
    from factory.runners.protocol import RunnerMeta

log = structlog.get_logger()


def _parse_usage(data: dict) -> AgentUsage:
    """Extract AgentUsage from Claude Code JSON output."""
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
            "--output-format", "json",
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

            result = await run_subprocess(
                cmd, cwd=str(request.cwd), env=env,
                timeout=request.timeout, runner_name="claude", role=request.role,
            )

            usage = None
            result_text = result.stdout
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    result_value = data.get("result", result.stdout)
                    result_text = result_value if isinstance(result_value, str) else result.stdout
                    usage = _parse_usage(data)
            except (json.JSONDecodeError, ValueError):
                log.debug("claude_json_parse_failed")

            return AgentRunResult(
                stdout=result_text,
                return_code=result.return_code,
                usage=usage,
                metadata=result.metadata,
            )
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Claude Code session as a subprocess."""
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(request.prompt)
            prompt_file.close()

            cmd = [
                "claude",
                "--append-system-prompt-file", prompt_file.name,
            ]
            if request.skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            cmd.append(request.task)
            if request.model:
                cmd.extend(["--model", request.model])
                os.environ["FACTORY_MODEL"] = request.model
            if request.session_name:
                cmd.extend(["--name", request.session_name])

            log.info("claude_interactive", cwd=str(request.cwd))

            result = subprocess.run(cmd, cwd=request.cwd)
            return result.returncode
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)
