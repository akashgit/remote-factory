"""Agent Runner v2 — unified abstraction for CLI backend implementations.

Runner Capability Matrix
========================
| Capability            | Claude  | Codex   | OpenCode | Implementation                                     |
|-----------------------|---------|---------|----------|----------------------------------------------------|
| model_override        | native  | native  | native   | --model flag                                       |
| system_prompt_file    | native  | proxy   | proxy    | temp file vs inline                                |
| append_system_prompt  | native  | proxy   | proxy    | --append-system-prompt vs fold into prompt          |
| tool_filtering        | native  | proxy   | proxy    | --allowedTools vs prompt injection                  |
| permission_modes      | native  | partial | partial  | --permission-mode vs --sandbox / --dangerously-skip |
| budget_cap            | native  | proxy   | proxy    | --max-budget-usd vs accept silently                 |
| effort_control        | native  | proxy   | native   | --effort vs prompt injection vs --variant           |
| structured_output     | native  | proxy   | native   | --output-format vs raw text vs --format             |
| session_resume        | native  | no      | native   | --resume/--name vs --continue/--session             |
| streaming             | native  | no      | no       | subprocess streaming                                |
| interactive           | native  | native  | native   | inherited stdio                                     |
| sandboxing            | no      | native  | no       | --sandbox                                           |
| mcp_config            | native  | no      | no       | --mcp-config                                        |
| usage_tracking        | native  | no      | partial  | JSON parse vs opencode stats                        |

native = CLI flag, proxy = emulated via prompt/workaround, partial = limited support, no = not available
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.models import AgentUsage

logger = logging.getLogger(__name__)


class Capability(Enum):
    """Capabilities that a runner backend may support."""

    MODEL_OVERRIDE = "model_override"
    SESSION_RESUME = "session_resume"
    SYSTEM_PROMPT_FILE = "system_prompt_file"
    STREAMING = "streaming"
    INTERACTIVE = "interactive"
    SANDBOXING = "sandboxing"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_FILTERING = "tool_filtering"
    PERMISSION_MODES = "permission_modes"
    BUDGET_CAP = "budget_cap"
    EFFORT_CONTROL = "effort_control"
    APPEND_SYSTEM_PROMPT = "append_system_prompt"
    MCP_CONFIG = "mcp_config"
    USAGE_TRACKING = "usage_tracking"


@dataclass
class RunnerIdentity:
    """Static metadata about a runner backend."""

    name: str
    cli_command: str
    capabilities: frozenset[Capability]


@dataclass
class Request:
    """Unified request for all runner backends."""

    prompt: str
    task: str
    cwd: Path
    timeout: float = 600.0
    model: str | None = None
    skip_permissions: bool = True
    role: str = "unknown"
    session_name: str | None = None
    tmux_persist: bool = False

    # v2 fields
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    max_budget_usd: float | None = None
    effort: str | None = None
    output_format: str | None = None
    append_system_prompt: str | None = None
    mcp_config: list[str] | None = field(default=None)


@dataclass
class Response:
    """Unified response from all runner backends."""

    stdout: str
    return_code: int
    usage: AgentUsage | None = None


class AgentRunner(abc.ABC):
    """Abstract base class for runner backends.

    Subclasses must implement:
    - ``identity`` property returning ``RunnerIdentity``
    - ``_build_command(request)`` returning the CLI command list
    - ``_parse_response(stdout, stderr, return_code)`` returning a ``Response``

    The ``run()`` method provides the subprocess lifecycle.
    Optional overrides:
    - ``_build_env()`` to customise the subprocess environment
    - ``check_health()`` to verify the CLI is available
    - ``_warn_unsupported(request)`` to log warnings for proxied features
    """

    @property
    @abc.abstractmethod
    def identity(self) -> RunnerIdentity:
        """Return static metadata about this runner."""

    @abc.abstractmethod
    def _build_command(self, request: Request) -> list[str]:
        """Build the CLI command for this request."""

    @abc.abstractmethod
    def _parse_response(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> Response:
        """Parse subprocess output into a Response."""

    def _build_env(self) -> dict[str, str]:
        """Build the subprocess environment. Override for custom env setup."""
        import os

        return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    def check_health(self) -> bool:
        """Check if the CLI tool is available on PATH."""
        import shutil

        return shutil.which(self.identity.cli_command) is not None

    def _warn_unsupported(self, request: Request) -> None:
        """Log warnings for request fields that are proxied or unsupported.

        Override in subclasses to add runner-specific warnings.
        """

    # -- Prompt injection helpers for runners without native support --

    def _inject_tool_restrictions(self, prompt: str, request: Request) -> str:
        """Inject allowed/disallowed tool instructions into prompt text."""
        lines: list[str] = []
        if request.allowed_tools:
            lines.append(
                f"IMPORTANT: You may ONLY use these tools: "
                f"{', '.join(request.allowed_tools)}. Do not use any other tools."
            )
        if request.disallowed_tools:
            lines.append(
                f"IMPORTANT: You must NOT use these tools: "
                f"{', '.join(request.disallowed_tools)}."
            )
        if lines:
            return prompt + "\n\n" + "\n".join(lines)
        return prompt

    def _inject_effort_instructions(self, prompt: str, effort: str | None) -> str:
        """Inject effort-level instructions into prompt text."""
        if not effort:
            return prompt
        effort_map = {
            "low": "Be concise and fast. Skip detailed analysis.",
            "medium": "Balance thoroughness with efficiency.",
            "high": "Be thorough. Think step by step. Consider edge cases.",
            "xhigh": "Be very thorough. Think carefully step by step. Explore possibilities and edge cases.",
            "max": (
                "Be extremely thorough. Think deeply step by step. "
                "Explore all possibilities. Double-check your work."
            ),
        }
        instruction = effort_map.get(effort, "")
        if instruction:
            return prompt + f"\n\nEFFORT LEVEL ({effort}): {instruction}"
        return prompt

    def _inject_append_system_prompt(self, prompt: str, extra: str | None) -> str:
        """Append additional system prompt text."""
        if extra:
            return prompt + "\n\n" + extra
        return prompt

    async def run(self, request: Request) -> Response:
        """Execute the full subprocess lifecycle.

        1. Warn about unsupported features
        2. Build the command
        3. Run the subprocess with streaming
        4. Parse the response
        """
        import asyncio

        from factory.runners._stream import should_stream, stream_subprocess

        self._warn_unsupported(request)

        cmd = self._build_command(request)
        env = self._build_env()
        if request.model:
            env["FACTORY_MODEL"] = request.model

        stream = should_stream()
        prefix = f"[{self.identity.name}:{request.role}]" if stream else None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=request.cwd,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                stream_subprocess(proc, stream=stream, prefix=prefix),
                timeout=request.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            logger.error("%s timed out after %ss", self.identity.name, request.timeout)
            return Response(
                stdout=f"Agent timed out after {request.timeout}s",
                return_code=1,
            )
        except FileNotFoundError:
            logger.error("'%s' CLI not found on PATH", self.identity.cli_command)
            return Response(
                stdout=f"Error: '{self.identity.cli_command}' CLI not found on PATH",
                return_code=1,
            )

        raw_stdout = stdout_bytes.decode()
        raw_stderr = stderr_bytes.decode()
        return_code = proc.returncode or 0

        if return_code != 0:
            logger.warning(
                "%s exited with code %d: %s",
                self.identity.name, return_code, raw_stderr[:200],
            )

        return self._parse_response(raw_stdout, raw_stderr, return_code)
