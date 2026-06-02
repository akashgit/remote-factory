"""CodexRunner — OpenAI Codex CLI backend implementation (v2)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.cli_adapter import CLIAdapter
from factory.runners.types import (
    RunnerCapability,
    RunnerRequest,
    RunnerResponse,
)

logger = logging.getLogger(__name__)

_auth_checked = False


class CodexAuthError(Exception):
    """Raised when neither CODEX_API_KEY nor OPENAI_API_KEY is set."""

    def __init__(self) -> None:
        super().__init__(
            "CODEX_API_KEY (or OPENAI_API_KEY) environment variable is not set. "
            "Set it directly or add it to a config.toml credential profile: "
            "[credentials.codex] CODEX_API_KEY = \"...\""
        )


def _check_auth() -> None:
    """Check that CODEX_API_KEY or OPENAI_API_KEY is set (once per process)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        _auth_checked = True
        return
    raise CodexAuthError()


def _make_codex_env() -> dict[str, str]:
    """Build subprocess env: strip VIRTUAL_ENV, ensure OPENAI_API_KEY is set."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
        env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]
    return env


def is_codex_dry_run() -> bool:
    """Return True if Codex dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("codex_dry_run", env_var="FACTORY_CODEX_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class CodexRunner(CLIAdapter):
    """Runner implementation for OpenAI Codex CLI."""

    name: str = "codex"

    def __init__(self) -> None:
        super().__init__(
            name="codex",
            display_name="OpenAI Codex",
            capabilities={RunnerCapability.MODEL_OVERRIDE, RunnerCapability.SANDBOXING},
            binary="codex",
        )

    async def check_health(self) -> tuple[bool, str]:
        ok, msg = await super().check_health()
        if not ok:
            return ok, msg
        if not (os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            return False, "CODEX_API_KEY or OPENAI_API_KEY not set"
        return True, "codex found and API key set"

    def _build_command(
        self,
        request: RunnerRequest,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        cmd = ["codex", "exec", request.prompt,
               "--sandbox", "workspace-write",
               "--ask-for-approval", "never"]
        if request.model:
            cmd.extend(["--model", request.model])
        return cmd

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> RunnerResponse:
        return RunnerResponse(output=stdout, exit_code=exit_code)

    def _build_env(self, request: RunnerRequest) -> dict[str, str]:
        env = super()._build_env(request)
        codex_key = env.get("CODEX_API_KEY")
        if codex_key and "OPENAI_API_KEY" not in env:
            env["OPENAI_API_KEY"] = codex_key
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
    ) -> tuple[str, int, None] | RunnerResponse:
        """Run a headless Codex CLI invocation.

        Supports both v1 (positional args -> tuple) and v2 (RunnerRequest -> RunnerResponse).
        """
        if isinstance(prompt, RunnerRequest):
            if is_codex_dry_run():
                return RunnerResponse(
                    output="[DRY-RUN] CodexRunner dry-run stub response.",
                    exit_code=0,
                )
            return await CLIAdapter.headless(self, prompt)

        # v1 path
        _ = session_name
        if tmux_persist:
            logger.warning("tmux_persist not supported with codex runner")
        if is_codex_dry_run():
            stdout, code = self._dry_run_response(role, Path(str(cwd)), task)
            return stdout, code, None

        _check_auth()

        full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = ["codex", "exec", full_prompt]

        if dangerously_skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

        if model:
            cmd.extend(["--model", model])

        logger.info("CodexRunner headless: cwd=%s, model=%s, role=%s", cwd, model, role)

        env = _make_codex_env()

        stream = should_stream()
        prefix = f"[codex:{role}]" if stream else None

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
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            logger.error("CodexRunner timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1, None
        except FileNotFoundError:
            logger.error("'codex' CLI not found on PATH")
            return "Error: 'codex' CLI not found on PATH", 1, None

        raw_stdout = stdout_bytes.decode()
        stderr_str = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning(
                "CodexRunner exited with code %d: %s", proc.returncode, stderr_str[:200],
            )

        return raw_stdout, proc.returncode or 0, None

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
        """Run an interactive Codex CLI session as a subprocess.

        Returns the exit code so the caller can clean up in a finally block.
        """
        _ = role, session_name

        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {task[:200]}...")
            return 0

        _check_auth()

        full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = ["codex", full_prompt]

        if dangerously_skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

        if model:
            cmd.extend(["--model", model])

        logger.info("CodexRunner interactive_run: cwd=%s", cwd)

        env = _make_codex_env()
        result = subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: Path, task: str) -> tuple[str, int]:
        """Return a stub response for dry-run mode."""
        response = (
            f"[DRY-RUN] CodexRunner would have executed:\n"
            f"  role: {role}\n"
            f"  cwd: {cwd}\n"
            f"  task: {task[:100]}...\n"
            f"\n"
            f"Dry-run stub response: Task acknowledged."
        )
        logger.info("CodexRunner dry-run: role=%s, cwd=%s", role, cwd)
        return response, 0
