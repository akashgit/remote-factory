"""Agent-runner abstraction — the operational interface for coding agents.

This module defines the core abstraction for controlling coding agent CLIs.
It is protocol-agnostic: it does not depend on ACP, MCP, or any specific
wire format. Any coding agent that can be invoked as a subprocess (Claude Code,
Codex, OpenCode, Goose, Aider, Cursor, etc.) can implement this interface.

The abstraction captures what a caller needs to DO with a coding agent,
not how the agent communicates internally:

    1. Set the agent's identity (system prompt)
    2. Give it a task
    3. Control its permissions and environment
    4. Get structured output back

Design principles:
    - Separate system prompt from task (different agents deliver these differently)
    - Runners inherit their native tools (the factory does NOT inject tools)
    - Capabilities are declared, not assumed
    - Health checks before dispatch
    - Usage and traces are optional (not every runner provides them)
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Capability Declaration ────────────────────────────────────────────────────
#
# Runners declare what they support. Callers check before using optional features.
# This replaces duck-typing guesswork with explicit negotiation.

class Capability(Enum):
    """What a runner supports. Callers check before using optional features."""
    MODEL_OVERRIDE = "model_override"       # Can switch LLM model via request
    SESSION_RESUME = "session_resume"       # Can resume a named session
    SYSTEM_PROMPT_FILE = "system_prompt_file"  # Delivers system prompt via file (not inline)
    STREAMING = "streaming"                 # Can stream output in real-time
    INTERACTIVE = "interactive"             # Can run with inherited stdio
    SANDBOXING = "sandboxing"               # Has built-in sandbox/permission control
    STRUCTURED_OUTPUT = "structured_output" # Returns parseable JSON/JSONL output


# ── Runner Identity ──────────────────────────────────────────────────────────

@dataclass
class RunnerIdentity:
    """Who this runner is and what it can do."""
    name: str                                      # CLI binary name: "claude", "codex"
    display_name: str                              # Human label: "Claude Code"
    version: str | None = None
    capabilities: set[Capability] = field(default_factory=set)


# ── Request ──────────────────────────────────────────────────────────────────
#
# The request separates system_prompt from task because different agents
# deliver them through different mechanisms:
#
#   Claude Code:  system_prompt → --append-system-prompt-file
#                 task          → -p "..."
#
#   Codex:        system_prompt + task → single positional arg to `codex exec`
#
#   OpenCode:     system_prompt + task → single positional arg to `opencode run`
#
#   Aider:        system_prompt → not supported (inline only)
#                 task          → --message "..."
#
# The .prompt property provides the combined string for agents that don't
# separate them. Agents that DO separate them use .system_prompt and .task
# directly in their _build_command().

@dataclass
class Request:
    """Everything a runner needs to execute a task."""
    system_prompt: str                             # Agent role definition (identity + playbook)
    task: str                                      # The work to do (user message)
    cwd: str                                       # Working directory for the agent
    timeout: int = 300                             # Max seconds before kill
    model: str | None = None                       # LLM model override (requires MODEL_OVERRIDE)
    session_name: str | None = None                # Session ID for resume (requires SESSION_RESUME)
    role: str | None = None                        # Caller's name for this invocation (logging)
    skip_permissions: bool = True                  # Auto-approve tool calls (headless mode)
    env: dict[str, str] = field(default_factory=dict)  # Extra env vars for subprocess

    @property
    def prompt(self) -> str:
        """Combined system_prompt + task. For agents that take a single input."""
        return f"{self.system_prompt}\n\n---\n\n## Current Task\n\n{self.task}"


# ── Response ─────────────────────────────────────────────────────────────────

@dataclass
class Usage:
    """Token consumption and cost. Optional — not every runner reports this."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    duration_seconds: float | None = None
    model_used: str | None = None


@dataclass
class Response:
    """What the runner produced."""
    output: str                                    # Final text output
    exit_code: int                                 # 0 = success
    usage: Usage | None = None                     # Token/cost data (if available)
    session_id: str | None = None                  # For session resume
    metadata: dict[str, Any] = field(default_factory=dict)


# ── The Abstraction ──────────────────────────────────────────────────────────
#
# This is the interface every agent-runner must implement.
#
# Key design decisions:
#
# 1. SYSTEM PROMPT IS SEPARATE FROM TASK.
#    The system prompt is the agent's identity (role definition, behavioral
#    playbook, constraints). The task is what the user wants done. These are
#    conceptually different and agents deliver them differently:
#    - Claude: system prompt via --append-system-prompt-file, task via -p
#    - Codex: everything inlined as a single argument
#    The abstraction preserves this separation so each runner can use its
#    native mechanism.
#
# 2. RUNNERS INHERIT THEIR NATIVE TOOLS.
#    Claude Code comes with Read, Edit, Bash, Grep, etc. Codex has its own.
#    The factory does NOT inject or declare tools — it trusts that each agent
#    has the tools it needs for software engineering tasks. The system prompt
#    can guide which tools to use, but the tools themselves are the agent's.
#
# 3. CAPABILITIES ARE DECLARED, NOT ASSUMED.
#    Before using model override, session resume, or other optional features,
#    callers check runner.identity.capabilities. This prevents runtime errors
#    from passing --model to an agent that doesn't support it.
#
# 4. HEALTH CHECKS ARE MANDATORY.
#    Every runner must be able to verify it's installed and ready. This catches
#    missing binaries, expired API keys, and misconfigured environments before
#    dispatching expensive work.

class AgentRunner(abc.ABC):
    """Base class for agent-runner implementations.

    Subclasses implement:
        - identity (property): who this runner is
        - check_health(): is it installed and ready?
        - _build_command(): how to invoke the CLI
        - _parse_response(): how to read the CLI output

    The base class handles:
        - Subprocess lifecycle (spawn, stream, timeout, cleanup)
        - System prompt delivery via temp file
        - Environment isolation (strip VIRTUAL_ENV, merge extras)
        - Interactive mode (inherited stdio)

    Minimal implementation example::

        class AiderRunner(AgentRunner):
            @property
            def identity(self) -> RunnerIdentity:
                return RunnerIdentity(name="aider", display_name="Aider")

            def _build_command(self, request, *, prompt_file=None):
                return ["aider", "--message", request.task, "--yes"]

            def _parse_response(self, stdout, stderr, exit_code):
                return Response(output=stdout, exit_code=exit_code)
    """

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or self.identity.name

    # -- Identity (subclass must implement) -----------------------------------

    @property
    @abc.abstractmethod
    def identity(self) -> RunnerIdentity:
        """Runner metadata and capabilities."""
        ...

    # -- Health ---------------------------------------------------------------

    async def check_health(self) -> tuple[bool, str]:
        """Verify the runner is installed and ready.

        Default: checks that the binary exists on PATH.
        Override to add auth checks (API keys, tokens, etc.).
        """
        if shutil.which(self._binary):
            return True, f"{self._binary} found"
        return False, f"{self._binary} not found in PATH"

    # -- Command construction (subclass must implement) -----------------------

    @abc.abstractmethod
    def _build_command(
        self,
        request: Request,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        """Build the CLI command to invoke the agent.

        Args:
            request: The execution request.
            prompt_file: Path to a temp file containing request.system_prompt.
                Runners that support SYSTEM_PROMPT_FILE use this path
                (e.g. claude --append-system-prompt-file <path>).
                Runners that inline everything can ignore it.

        Returns:
            Command as a list of strings (for subprocess).
        """
        ...

    # -- Output parsing (subclass must implement) -----------------------------

    @abc.abstractmethod
    def _parse_response(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> Response:
        """Parse subprocess output into a structured Response.

        This is where runner-specific output formats are handled:
        - Claude: parse stream-json JSONL → extract text, usage, session_id
        - Codex: parse --json output → extract result
        - Others: return raw stdout as output
        """
        ...

    # -- Environment ----------------------------------------------------------

    def _build_env(self, request: Request) -> dict[str, str]:
        """Build subprocess environment.

        Default: inherit everything except VIRTUAL_ENV, merge request.env.
        Override to add runner-specific env setup (API key mapping, etc.).
        """
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        env.update(request.env)
        return env

    # -- Execution (shared infrastructure) ------------------------------------

    async def run(self, request: Request) -> Response:
        """Execute a headless agent invocation.

        Handles the full subprocess lifecycle:
        1. Write system_prompt to a temp file
        2. Build CLI command via _build_command()
        3. Spawn subprocess with streaming
        4. Enforce timeout
        5. Parse output via _parse_response()
        6. Clean up temp file
        """
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="agent-prompt-", delete=False,
        )
        try:
            fd.write(request.system_prompt)
            fd.close()

            cmd = self._build_command(request, prompt_file=fd.name)
            env = self._build_env(request)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=request.cwd,
                    env=env,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=request.timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()  # type: ignore[union-attr]
                await proc.wait()  # type: ignore[union-attr]
                return Response(
                    output=f"Agent timed out after {request.timeout}s",
                    exit_code=1,
                )
            except FileNotFoundError:
                return Response(
                    output=f"Error: '{self._binary}' not found on PATH",
                    exit_code=1,
                )

            return self._parse_response(
                stdout_bytes.decode(),
                stderr_bytes.decode(),
                proc.returncode or 0,
            )
        finally:
            Path(fd.name).unlink(missing_ok=True)

    def run_interactive(self, request: Request) -> Response:
        """Run an interactive session with inherited stdio.

        The user sees the agent's TUI directly. No output capture.
        """
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="agent-prompt-", delete=False,
        )
        try:
            fd.write(request.system_prompt)
            fd.close()
            cmd = self._build_command(request, prompt_file=fd.name)
            result = subprocess.run(cmd, cwd=request.cwd)
            return Response(output="", exit_code=result.returncode)
        finally:
            Path(fd.name).unlink(missing_ok=True)


# ── Example Implementations ─────────────────────────────────────────────────
#
# These show how different agents map to the abstraction.
# Each is ~20 lines. The abstraction handles everything else.


class ClaudeCodeRunner(AgentRunner):
    """Claude Code — separates system prompt from task via native CLI flags."""

    @property
    def identity(self) -> RunnerIdentity:
        return RunnerIdentity(
            name="claude",
            display_name="Claude Code",
            capabilities={
                Capability.MODEL_OVERRIDE,
                Capability.SESSION_RESUME,
                Capability.SYSTEM_PROMPT_FILE,
                Capability.STRUCTURED_OUTPUT,
                Capability.STREAMING,
                Capability.INTERACTIVE,
            },
        )

    def _build_command(self, request, *, prompt_file=None):
        cmd = ["claude"]
        if prompt_file:
            cmd.extend(["--append-system-prompt-file", prompt_file])
        cmd.extend(["-p", request.task, "--output-format", "stream-json"])
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])
        return cmd

    def _parse_response(self, stdout, stderr, exit_code):
        # In production: parse stream-json JSONL → extract text, usage, traces
        # (see factory/runners/claude.py for the full parser)
        return Response(output=stdout, exit_code=exit_code)


class CodexCLIRunner(AgentRunner):
    """OpenAI Codex — inlines everything as a single exec argument."""

    @property
    def identity(self) -> RunnerIdentity:
        return RunnerIdentity(
            name="codex",
            display_name="OpenAI Codex",
            capabilities={
                Capability.MODEL_OVERRIDE,
                Capability.SANDBOXING,
            },
        )

    async def check_health(self):
        ok, msg = await super().check_health()
        if not ok:
            return ok, msg
        if not (os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            return False, "CODEX_API_KEY or OPENAI_API_KEY not set"
        return True, "codex ready"

    def _build_command(self, request, *, prompt_file=None):
        # Codex takes a single prompt — use .prompt to combine system + task
        cmd = ["codex", "exec", request.prompt,
               "--sandbox", "workspace-write",
               "--ask-for-approval", "never"]
        if request.model:
            cmd.extend(["--model", request.model])
        return cmd

    def _build_env(self, request):
        env = super()._build_env(request)
        # Map CODEX_API_KEY → OPENAI_API_KEY if needed
        if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
            env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]
        return env

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)


class OpenCodeCLIRunner(AgentRunner):
    """OpenCode — inlines everything, returns JSON."""

    @property
    def identity(self) -> RunnerIdentity:
        return RunnerIdentity(
            name="opencode",
            display_name="OpenCode",
            capabilities={
                Capability.MODEL_OVERRIDE,
                Capability.STRUCTURED_OUTPUT,
            },
        )

    def _build_command(self, request, *, prompt_file=None):
        return ["opencode", "run", "--format", "json", request.prompt]

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)


class AiderRunner(AgentRunner):
    """Aider — no system prompt separation, no structured output."""

    @property
    def identity(self) -> RunnerIdentity:
        return RunnerIdentity(name="aider", display_name="Aider")

    def _build_command(self, request, *, prompt_file=None):
        # Aider has no system prompt mechanism — inline everything via --message
        return ["aider", "--message", request.prompt, "--yes"]

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)


class GooseRunner(AgentRunner):
    """Goose — inlines prompt, optional JSON output."""

    @property
    def identity(self) -> RunnerIdentity:
        return RunnerIdentity(
            name="goose",
            display_name="Goose",
            capabilities={Capability.STRUCTURED_OUTPUT},
        )

    def _build_command(self, request, *, prompt_file=None):
        return ["goose", "-t", request.prompt, "--output", "json"]

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)
