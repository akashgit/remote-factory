"""CodexRunner — OpenAI Codex CLI backend implementation."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult
    from factory.runners.protocol import RunnerMeta

log = structlog.get_logger()

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


def _make_codex_env() -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    """Build subprocess env with auth isolation.

    Returns (env_dict, tmpdir_handle) — caller must keep tmpdir_handle alive
    until the subprocess exits, then call .cleanup().
    """
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
        env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]
    tmpdir = tempfile.TemporaryDirectory(prefix="factory-codex-")
    env["CODEX_HOME"] = tmpdir.name
    return env, tmpdir


def is_codex_dry_run() -> bool:
    """Return True if Codex dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("codex_dry_run", env_var="FACTORY_CODEX_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class CodexRunner:
    """Runner implementation for OpenAI Codex CLI."""

    name: str = "codex"

    @classmethod
    def metadata(cls) -> RunnerMeta:
        from factory.runners.protocol import RunnerMeta
        return RunnerMeta(
            name="codex",
            display_name="OpenAI Codex",
            binary="codex",
            install_hint="npm install -g @openai/codex",
            required_env_vars=["OPENAI_API_KEY"],
            supports_usage_telemetry=False,
            supports_session_name=False,
        )

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Codex CLI invocation via ``codex exec``."""
        from factory.models import AgentRunResult

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            log.warning("codex_tmux_not_supported")
        if is_codex_dry_run():
            stdout, code = self._dry_run_response(request.role, request.cwd, request.task)
            return AgentRunResult(stdout=stdout, return_code=code)

        _check_auth()

        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = ["codex", "exec", full_prompt, "--ignore-user-config"]

        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

        if request.model:
            cmd.extend(["--model", request.model])

        log.info("codex_headless", cwd=str(request.cwd), model=request.model, role=request.role)

        env, tmpdir = _make_codex_env()
        try:
            return await run_subprocess(
                cmd, cwd=str(request.cwd), env=env,
                timeout=request.timeout, runner_name="codex", role=request.role,
            )
        finally:
            tmpdir.cleanup()

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Codex CLI session as a subprocess."""
        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth()

        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = ["codex", full_prompt, "--ignore-user-config"]

        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

        if request.model:
            cmd.extend(["--model", request.model])

        log.info("codex_interactive", cwd=str(request.cwd))

        env, tmpdir = _make_codex_env()
        try:
            result = subprocess.run(cmd, cwd=request.cwd, env=env)
            return result.returncode
        finally:
            tmpdir.cleanup()

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
        log.info("codex_dry_run", role=role, cwd=str(cwd))
        return response, 0
