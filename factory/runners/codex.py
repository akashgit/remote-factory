"""CodexRunner — OpenAI Codex CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess

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


class CodexRunner:
    """Runner implementation for OpenAI Codex CLI."""

    name: str = "codex"

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
    ) -> tuple[str, int]:
        """Run a headless Codex CLI invocation via ``codex exec``.

        Codex exec streams progress to stderr and writes only the final
        agent message to stdout, which aligns with the factory's capture model.

        Returns (stdout, return_code).
        """
        _ = session_name
        if is_codex_dry_run():
            return self._dry_run_response(role, cwd, task)

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
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("'codex' CLI not found on PATH")
            return "Error: 'codex' CLI not found on PATH", 1

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning("CodexRunner exited with code %d: %s", proc.returncode, stderr[:200])

        return stdout, proc.returncode or 0

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
