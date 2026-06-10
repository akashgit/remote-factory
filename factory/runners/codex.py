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
    _agents_md_path: Path | None = None
    _agents_md_backup: str | None = None

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

    def _setup_agents_md(self, cwd: Path, prompt: str) -> None:
        """Write the system prompt to AGENTS.md, backing up any existing content."""
        agents_md = cwd / "AGENTS.md"
        self._agents_md_path = agents_md
        self._agents_md_backup = None
        if agents_md.is_file():
            self._agents_md_backup = agents_md.read_text()
            agents_md.write_text(f"{self._agents_md_backup}\n\n{prompt}")
        else:
            agents_md.write_text(prompt)

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the Codex CLI command, env dict, and temp files."""
        cmd = ["codex", "exec"]

        if _using_api_key():
            cmd.append("--ignore-user-config")

        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write"])

        if request.model:
            cmd.extend(["--model", request.model])

        cmd.append("--skip-git-repo-check")
        cmd.extend(["--", request.task])

        env, tmpdir = _make_codex_env()
        self._tmpdir = tmpdir
        return cmd, env, []

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Codex CLI invocation via ``codex exec``."""
        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            log.warning("codex_tmux_not_supported")
        if is_codex_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            return make_dry_run_result("codex", request.role, request.cwd, request.task)

        _check_auth()

        self._setup_agents_md(request.cwd, request.prompt)
        try:
            cmd, env, _ = self.build_command(request)

            log.info("codex_headless", cwd=str(request.cwd), model=request.model, role=request.role)

            retried = False
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
            self._restore_agents_md()
            if hasattr(self, "_tmpdir") and self._tmpdir is not None:
                self._tmpdir.cleanup()

    def _restore_agents_md(self) -> None:
        """Restore the original AGENTS.md content after a Codex invocation."""
        agents_md = getattr(self, "_agents_md_path", None)
        if agents_md is None:
            return
        backup = getattr(self, "_agents_md_backup", None)
        try:
            if backup is not None:
                agents_md.write_text(backup)
            elif agents_md.is_file():
                agents_md.unlink()
        except OSError:
            log.debug("codex_agents_md_restore_failed", exc_info=True)
        self._agents_md_path = None
        self._agents_md_backup = None

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Codex CLI session as a subprocess."""
        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth()

        self._setup_agents_md(request.cwd, request.prompt)
        try:
            cmd = ["codex", request.task]

            if _using_api_key():
                cmd.append("--ignore-user-config")

            if request.skip_permissions:
                cmd.append("--full-auto")

            if request.model:
                cmd.extend(["--model", request.model])

            log.info("codex_interactive", cwd=str(request.cwd))

            env, tmpdir = _make_codex_env()
            try:
                result = subprocess.run(cmd, cwd=request.cwd, env=env)
                return result.returncode
            finally:
                if tmpdir is not None:
                    tmpdir.cleanup()
        finally:
            self._restore_agents_md()

