"""CodexRunner — OpenAI Codex CLI backend implementation."""

from __future__ import annotations

import asyncio
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


def _has_codex_oauth() -> bool:
    """Check if Codex has OAuth credentials in its default config."""
    auth_file = Path.home() / ".codex" / "auth.json"
    return auth_file.is_file()


def _using_api_key() -> bool:
    """Return True if an explicit API key is set in the environment."""
    return bool(os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _check_auth() -> None:
    """Check that Codex auth is available (OAuth preferred, then API key)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if _has_codex_oauth():
        log.info("codex_oauth_detected")
        _auth_checked = True
        return
    if _using_api_key():
        _auth_checked = True
        return
    raise CodexAuthError()


def _make_codex_env() -> tuple[dict[str, str], tempfile.TemporaryDirectory[str] | None]:
    """Build subprocess env with auth isolation.

    OAuth is preferred when ~/.codex/auth.json exists — OPENAI_API_KEY is
    stripped from the env so Codex doesn't switch to API key mode (which
    can cause 401 errors when the key lacks Responses API scopes).

    In API key mode, sets CODEX_HOME to a temp dir to avoid stale OAuth.

    Returns (env_dict, tmpdir_handle_or_None) — caller must keep tmpdir_handle
    alive until the subprocess exits, then call .cleanup() if not None.
    """
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    if _has_codex_oauth():
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        return env, None

    if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
        env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]

    if _using_api_key():
        tmpdir = tempfile.TemporaryDirectory(prefix="factory-codex-")
        env["CODEX_HOME"] = tmpdir.name
        return env, tmpdir

    return env, None


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

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the Codex CLI command, env dict, and temp files."""
        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = ["codex", "exec"]

        if _using_api_key():
            cmd.append("--ignore-user-config")

        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write"])

        if request.model:
            cmd.extend(["--model", request.model])

        cmd.append("--skip-git-repo-check")
        cmd.extend(["--", full_prompt])

        env, tmpdir = _make_codex_env()
        self._tmpdir = tmpdir
        return cmd, env, []

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Codex CLI invocation via ``codex exec``."""
        from factory.models import AgentRunResult

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            return AgentRunResult(
                stdout="Error: --tmux-persist is not supported with the codex runner. Use --runner claude.",
                return_code=1,
            )
        background = request.extras.get("background", False)
        if background:
            log.warning("codex_bg_not_supported", hint="--bg is a claude-only feature")
        if is_codex_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            return make_dry_run_result("codex", request.role, request.cwd, request.task)

        _check_auth()

        cmd, env, _ = self.build_command(request)

        log.info("codex_headless", cwd=str(request.cwd), model=request.model, role=request.role)

        retried = False
        try:
            result = await run_subprocess(
                cmd, cwd=str(request.cwd), env=env,
                timeout=request.timeout, runner_name="codex", role=request.role,
            )
            stderr = str(result.metadata.get("stderr", ""))
            if "401 Unauthorized" in stderr and not retried:
                retried = True
                log.warning("codex_auth_retry", reason="401 Unauthorized in stderr")
                await asyncio.sleep(2)
                result = await run_subprocess(
                    cmd, cwd=str(request.cwd), env=env,
                    timeout=request.timeout, runner_name="codex", role=request.role,
                )
            return result
        finally:
            if hasattr(self, "_tmpdir") and self._tmpdir is not None:
                self._tmpdir.cleanup()

    def build_interactive_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the CLI command, env dict, and temp files for an interactive invocation."""
        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = ["codex", full_prompt]

        if _using_api_key():
            cmd.append("--ignore-user-config")

        if request.skip_permissions:
            cmd.append("--full-auto")

        if request.model:
            cmd.extend(["--model", request.model])

        env, tmpdir = _make_codex_env()
        self._tmpdir = tmpdir
        return cmd, env, []

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Codex CLI session as a subprocess."""
        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth()

        cmd, env, _ = self.build_interactive_command(request)
        try:
            log.info("codex_interactive", cwd=str(request.cwd))
            result = subprocess.run(cmd, cwd=request.cwd, env=env)
            return result.returncode
        finally:
            if hasattr(self, "_tmpdir") and self._tmpdir is not None:
                self._tmpdir.cleanup()

