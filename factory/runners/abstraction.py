"""Agent-Runner abstraction — base class and core types for CLI backends.

Concrete runners implement three methods:
  - identity (property): declares name, display_name, binary, capabilities
  - _build_command(request, *, prompt_file): maps Request fields to CLI args
  - _parse_response(stdout, stderr, exit_code): extracts Response from raw output

The base class handles the shared subprocess lifecycle (temp file management,
env isolation, streaming, timeout enforcement) via run() and run_interactive().
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from factory.runners._stream import should_stream, stream_subprocess

if TYPE_CHECKING:
    from factory.models import AgentUsage

logger = logging.getLogger(__name__)


# ── Core types ────────────────────────────────────────────────────


@dataclass
class Request:
    """Describes what to run and how."""

    system_prompt: str
    task: str
    cwd: str | Path
    timeout: float = 600.0
    model: str | None = None
    skip_permissions: bool = True
    session_name: str | None = None
    env: dict[str, str] | None = None
    tmux_persist: bool = False
    role: str = "unknown"

    @property
    def prompt(self) -> str:
        """Combined system_prompt + task for agents that don't separate them."""
        return f"{self.system_prompt}\n\n---\n\n## Current Task\n\n{self.task}"


@dataclass
class Response:
    """Result from an agent invocation."""

    output: str
    exit_code: int
    usage: AgentUsage | None = None
    error: str | None = None


class Capability(Enum):
    """Optional features a runner may support."""

    MODEL_OVERRIDE = "model_override"
    SESSION_RESUME = "session_resume"
    SYSTEM_PROMPT_FILE = "system_prompt_file"
    STREAMING = "streaming"
    INTERACTIVE = "interactive"
    SANDBOXING = "sandboxing"
    STRUCTURED_OUTPUT = "structured_output"


@dataclass
class RunnerIdentity:
    """Static metadata about a runner implementation."""

    name: str
    display_name: str
    binary: str = ""
    capabilities: set[Capability] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.binary:
            self.binary = self.name


# ── Base class ────────────────────────────────────────────────────


class AgentRunner(ABC):
    """Abstract base class for CLI agent backends."""

    @property
    @abstractmethod
    def identity(self) -> RunnerIdentity:
        """Return static metadata about this runner."""
        ...

    @abstractmethod
    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        """Build the CLI command list for the given request.

        Args:
            request: The agent request.
            prompt_file: Path to a temp file containing the system prompt.
        """
        ...

    @abstractmethod
    def _parse_response(
        self, stdout: str, stderr: str, exit_code: int
    ) -> Response:
        """Parse raw subprocess output into a Response."""
        ...

    def _build_env(self, request: Request) -> dict[str, str]:
        """Build the subprocess environment.

        Base implementation copies os.environ, strips VIRTUAL_ENV,
        and merges any extra env vars from the request.
        """
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        if request.env:
            env.update(request.env)
        return env

    async def check_health(self) -> tuple[bool, str]:
        """Check whether this runner's binary is available on PATH."""
        binary = self.identity.binary
        path = shutil.which(binary)
        if path:
            return True, f"{self.identity.display_name} found at {path}"
        return False, f"'{binary}' not found in PATH"

    async def run(self, request: Request) -> Response:
        """Run a headless agent invocation.

        Lifecycle:
        1. Write system_prompt to a temp file
        2. Build env via _build_env()
        3. Build command via _build_command()
        4. Spawn subprocess, stream output via stream_subprocess()
        5. Enforce timeout (kill on TimeoutError)
        6. Parse output via _parse_response()
        7. Clean up temp file
        """
        cwd = Path(request.cwd)
        ident = self.identity

        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(request.system_prompt)
            prompt_file.close()

            env = self._build_env(request)
            cmd = self._build_command(request, prompt_file=prompt_file.name)

            logger.info(
                "%s run: cwd=%s, model=%s, role=%s",
                ident.display_name, cwd, request.model, request.role,
            )

            stream = should_stream()
            prefix = f"[{ident.name}:{request.role}]" if stream else None

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    stream_subprocess(proc, stream=stream, prefix=prefix),
                    timeout=request.timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()  # type: ignore[union-attr]
                await proc.wait()  # type: ignore[union-attr]
                logger.error(
                    "%s timed out after %ss", ident.display_name, request.timeout,
                )
                return Response(
                    output=f"Agent timed out after {request.timeout}s",
                    exit_code=1,
                    error=f"Timeout after {request.timeout}s",
                )
            except FileNotFoundError:
                logger.error("'%s' CLI not found on PATH", ident.binary)
                return Response(
                    output=f"Error: '{ident.binary}' CLI not found on PATH",
                    exit_code=1,
                    error=f"'{ident.binary}' not found on PATH",
                )

            raw_stdout = stdout_bytes.decode()
            raw_stderr = stderr_bytes.decode()
            return_code = proc.returncode or 0

            if return_code != 0:
                logger.warning(
                    "%s exited with code %d: %s",
                    ident.display_name, return_code, raw_stderr[:200],
                )

            return self._parse_response(raw_stdout, raw_stderr, return_code)
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)

    def run_interactive(self, request: Request) -> int:
        """Run an interactive session with inherited stdio.

        Returns the subprocess exit code.
        """
        cwd = Path(request.cwd)
        ident = self.identity

        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(request.system_prompt)
            prompt_file.close()

            cmd = self._build_command(request, prompt_file=prompt_file.name)

            logger.info(
                "%s interactive: cwd=%s, role=%s",
                ident.display_name, cwd, request.role,
            )

            result = subprocess.run(cmd, cwd=cwd)
            return result.returncode
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)

    # ── Backward-compat shims (Phase 2 migration) ────────────────

    async def headless(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        timeout: float = 600.0,
        model: str | None = None,
        dangerously_skip_permissions: bool = True,
        role: str = "unknown",
        session_name: str | None = None,
        tmux_persist: bool = False,
    ) -> tuple[str, int, AgentUsage | None]:
        """Backward-compat shim — delegates to run()."""
        request = Request(
            system_prompt=prompt,
            task=task,
            cwd=cwd,
            timeout=timeout,
            model=model,
            skip_permissions=dangerously_skip_permissions,
            session_name=session_name,
            tmux_persist=tmux_persist,
            role=role,
        )
        response = await self.run(request)
        return response.output, response.exit_code, response.usage

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
        """Backward-compat shim — delegates to run_interactive()."""
        request = Request(
            system_prompt=prompt,
            task=task,
            cwd=cwd,
            model=model,
            skip_permissions=dangerously_skip_permissions,
            session_name=session_name,
            role=role,
        )
        return self.run_interactive(request)
