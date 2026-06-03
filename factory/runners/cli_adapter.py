"""CLIAdapter — abstract base class for direct CLI backend adapters."""

from __future__ import annotations

import abc
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.types import (
    RunnerCapability,
    RunnerInfo,
    RunnerRequest,
    RunnerResponse,
)

logger = logging.getLogger(__name__)


class CLIAdapter(abc.ABC):
    """Abstract base class for CLI backend adapters.

    Provides shared subprocess management (spawn, stream, timeout, env)
    while delegating command construction and output parsing to subclasses.

    Subclasses implement:
        - _build_command(): how to invoke the CLI
        - _parse_output(): how to read the CLI output

    Optionally override:
        - _build_env(): env var setup
        - _inject_prompt_proxy(): inline unsupported features into the prompt
        - check_health(): binary + auth checks
    """

    def __init__(
        self,
        name: str,
        display_name: str,
        capabilities: set[RunnerCapability] | None = None,
        binary: str | None = None,
    ) -> None:
        self._name = name
        self._display_name = display_name
        self._capabilities = capabilities or set()
        self._binary = binary or name

    @property
    def info(self) -> RunnerInfo:
        return RunnerInfo(
            name=self._name,
            display_name=self._display_name,
            capabilities=self._capabilities,
        )

    async def check_health(self) -> tuple[bool, str]:
        if shutil.which(self._binary):
            return True, f"{self._binary} found"
        return False, f"{self._binary} not found in PATH"

    @abc.abstractmethod
    def _build_command(
        self,
        request: RunnerRequest,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        """Build the CLI command for the given request.

        Args:
            request: The runner request.
            prompt_file: Path to a temp file containing the full system prompt,
                         created by headless()/interactive() for adapters
                         that deliver prompts via file (e.g. --append-system-prompt-file).
        """
        ...

    @abc.abstractmethod
    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> RunnerResponse:
        """Parse subprocess output into a structured RunnerResponse."""
        ...

    def _build_env(self, request: RunnerRequest) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        env.update(request.env_overrides)
        return env

    def _inject_prompt_proxy(self, request: RunnerRequest) -> str:
        """Build prompt-proxy instructions for features this runner doesn't support natively.

        Called by _write_system_prompt(). Subclasses that support features natively
        (e.g. Claude's --allowedTools) should override to skip those.

        Returns extra text to append to the system prompt, or empty string.
        """
        parts: list[str] = []

        if request.allowed_tools:
            tools = ", ".join(request.allowed_tools)
            parts.append(f"IMPORTANT: You may ONLY use these tools: {tools}. Do not use any other tools.")

        if request.disallowed_tools:
            tools = ", ".join(request.disallowed_tools)
            parts.append(f"IMPORTANT: You must NOT use these tools: {tools}.")

        if request.max_turns is not None:
            parts.append(
                f"IMPORTANT: Complete your work within {request.max_turns} conversation turns. "
                "Be efficient and avoid unnecessary back-and-forth."
            )

        if request.max_tokens is not None:
            parts.append(
                f"IMPORTANT: Keep your total output under {request.max_tokens} tokens. Be concise."
            )

        if request.max_cost_usd is not None:
            parts.append(
                f"IMPORTANT: This invocation has a budget of ${request.max_cost_usd:.2f}. "
                "Minimize token usage. Avoid reading large files unnecessarily."
            )

        from factory.runners.types import SandboxMode
        if request.sandbox_mode == SandboxMode.READ_ONLY:
            parts.append(
                "IMPORTANT: READ-ONLY MODE. Do not write, edit, or delete any files. "
                "Do not execute commands that modify the filesystem."
            )

        return "\n\n".join(parts)

    def _write_system_prompt(self, request: RunnerRequest) -> str:
        """Assemble and write the full system prompt to a temp file.

        Combines: base system_prompt + appended sections + file contents + prompt proxy.
        Returns the temp file path. Caller is responsible for cleanup.
        """
        content = request.full_system_prompt

        proxy = self._inject_prompt_proxy(request)
        if proxy:
            content = f"{content}\n\n---\n\n{proxy}"

        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        fd.write(content)
        fd.close()
        return fd.name

    async def headless(self, request: RunnerRequest) -> RunnerResponse:
        """Run a headless (non-interactive) agent invocation."""
        prompt_path = self._write_system_prompt(request)
        try:
            cmd = self._build_command(request, prompt_file=prompt_path)
            env = self._build_env(request)

            stream = should_stream()
            prefix = f"[{self._name}:{request.role or 'unknown'}]" if stream else None

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
                logger.error("%s timed out after %ss", self._display_name, request.timeout)
                return RunnerResponse(
                    output=f"Agent timed out after {request.timeout}s",
                    exit_code=1,
                )
            except FileNotFoundError:
                logger.error("'%s' CLI not found on PATH", self._binary)
                return RunnerResponse(
                    output=f"Error: '{self._binary}' CLI not found on PATH",
                    exit_code=1,
                )

            raw_stdout = stdout_bytes.decode()
            raw_stderr = stderr_bytes.decode()
            return_code = proc.returncode or 0

            if return_code != 0:
                import sys as _sys
                logger.error(
                    "%s exited with code %d: stderr=%s stdout=%s",
                    self._display_name, return_code, raw_stderr[:300], raw_stdout[:300],
                )
                print(
                    f"[{self._name}] FAILED (exit={return_code}): {raw_stderr[:200]}",
                    file=_sys.stderr,
                )

            return self._parse_output(raw_stdout, raw_stderr, return_code)
        finally:
            Path(prompt_path).unlink(missing_ok=True)

    def interactive(self, request: RunnerRequest) -> RunnerResponse:
        """Run an interactive CLI session with inherited stdio."""
        prompt_path = self._write_system_prompt(request)
        try:
            cmd = self._build_command(request, prompt_file=prompt_path)

            logger.info("%s interactive: cwd=%s", self._display_name, request.cwd)

            result = subprocess.run(cmd, cwd=request.cwd)
            return RunnerResponse(output="", exit_code=result.returncode)
        finally:
            Path(prompt_path).unlink(missing_ok=True)
