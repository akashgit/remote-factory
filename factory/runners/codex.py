"""CodexRunner — OpenAI Codex CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
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


_CODEX_IDENTITY = RunnerIdentity(
    name="codex",
    cli_command="codex",
    capabilities=frozenset({
        Capability.MODEL_OVERRIDE,
        Capability.INTERACTIVE,
        Capability.SANDBOXING,
    }),
)


class CodexRunner(AgentRunner):
    """Runner implementation for OpenAI Codex CLI."""

    name: str = "codex"

    @property
    def identity(self) -> RunnerIdentity:
        return _CODEX_IDENTITY

    def _build_command(self, request: Request) -> list[str]:
        """Build the codex CLI command.

        Tool filtering, effort, and append_system_prompt are proxied via prompt
        injection (handled in headless() where the full prompt is assembled).
        """
        cmd = ["codex", "exec"]

        # Permission handling
        if request.permission_mode:
            if request.permission_mode == "bypassPermissions":
                cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])
        elif request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

        if request.model:
            cmd.extend(["--model", request.model])

        return cmd

    def _build_env(self) -> dict[str, str]:
        return _make_codex_env()

    def _parse_response(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> Response:
        """Parse codex output. No usage telemetry available."""
        return Response(stdout=stdout, return_code=return_code, usage=None)

    def _warn_unsupported(self, request: Request) -> None:
        """Log warnings for features proxied via prompt injection."""
        if request.max_budget_usd is not None:
            logger.warning(
                "CodexRunner: max_budget_usd=%.2f accepted but not natively enforced",
                request.max_budget_usd,
            )
        if request.mcp_config:
            logger.warning("CodexRunner: mcp_config is not supported by codex, ignoring")

    def _build_full_prompt(self, prompt: str, task: str, request: Request) -> str:
        """Build the combined prompt with proxied features folded in."""
        full = prompt
        full = self._inject_tool_restrictions(full, request)
        full = self._inject_effort_instructions(full, request.effort)
        full = self._inject_append_system_prompt(full, request.append_system_prompt)
        return f"{full}\n\n---\n\n## Current Task\n\n{task}"

    # -- Backward-compatible headless() shim --

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
    ) -> tuple[str, int, None]:
        """Run a headless Codex CLI invocation (backward-compat shim)."""
        _ = session_name
        if tmux_persist:
            logger.warning("tmux_persist not supported with codex runner")
        if is_codex_dry_run():
            stdout, code = self._dry_run_response(role, cwd, task)
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

        stdout_str = stdout_bytes.decode()
        stderr_str = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning("CodexRunner exited with code %d: %s", proc.returncode, stderr_str[:200])

        return stdout_str, proc.returncode or 0, None

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
        """Run an interactive Codex CLI session as a subprocess."""
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
