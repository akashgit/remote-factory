"""CodexRunner — OpenAI Codex CLI backend implementation."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult, AgentUsage
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


def _parse_codex_ndjson_usage(raw_output: str) -> AgentUsage | None:
    """Parse Codex NDJSON output for token usage events."""
    from factory.models import AgentUsage

    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    model = ""

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        event_type = event.get("type", "")

        if event_type == "usage":
            usage = event.get("usage", {})
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            reasoning_tokens += usage.get("reasoning_tokens", 0)
            if event.get("model"):
                model = event["model"]

    if input_tokens == 0 and output_tokens == 0:
        return None

    return AgentUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens + reasoning_tokens,
        model=model,
    )


def _write_agents_md(project_root: Path, role_prompt: str) -> tuple[Path, Path | None]:
    """Write an AGENTS.md file at the project root for Codex agent discovery.

    If AGENTS.md already exists, backs it up so it can be restored on cleanup.
    Returns (agents_md_path, backup_path_or_None).
    """
    agents_md = project_root / "AGENTS.md"
    backup: Path | None = None
    backup_path = project_root / ".AGENTS.md.factory-backup"
    if agents_md.exists():
        if backup_path.exists():
            backup = backup_path
        else:
            agents_md.rename(backup_path)
            backup = backup_path
    agents_md.write_text(role_prompt)
    return agents_md, backup


def _cleanup_agents_md(agents_md: Path, backup: Path | None) -> None:
    """Remove factory-written AGENTS.md and restore backup if one existed."""
    agents_md.unlink(missing_ok=True)
    if backup is not None and backup.exists():
        backup.rename(agents_md)


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
            supports_usage_telemetry=True,
            supports_session_name=False,
        )

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the Codex CLI command, env dict, and temp files."""
        temp_files: list[Path] = []

        project_root = request.project_path or request.cwd
        agents_md, self._agents_md_backup = _write_agents_md(project_root, request.prompt)
        temp_files.append(agents_md)

        cmd = ["codex", "exec"]

        if _using_api_key():
            cmd.append("--ignore-user-config")

        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write"])

        if request.model:
            cmd.extend(["--model", request.model])

        cmd.append("--skip-git-repo-check")
        cmd.append("--json")
        cmd.extend(["--", request.task])

        env, tmpdir = _make_codex_env()
        self._tmpdir = tmpdir
        return cmd, env, temp_files

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Codex CLI invocation via ``codex exec``."""
        from factory.models import AgentRunResult

        if is_codex_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            return make_dry_run_result("codex", request.role, request.cwd, request.task)

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            from factory.runners._tmux_persist import find_project_path, run_in_tmux, tmux_available

            if tmux_available():
                project_path = find_project_path(request.cwd)
                int_cmd, int_env, int_temp = self.build_interactive_command(request)
                try:
                    stdout, rc, usage = await run_in_tmux(
                        request.prompt, request.task, request.cwd, request.role,
                        project_path,
                        runner_cmd=int_cmd,
                        runner_env=int_env,
                    )
                    return AgentRunResult(stdout=stdout, return_code=rc, usage=usage)
                finally:
                    backup = getattr(self, "_agents_md_backup", None)
                    for f in int_temp:
                        if f.name == "AGENTS.md":
                            _cleanup_agents_md(f, backup)
                        else:
                            f.unlink(missing_ok=True)
                    self._agents_md_backup = None
                    if hasattr(self, "_tmpdir") and self._tmpdir is not None:
                        self._tmpdir.cleanup()
                        self._tmpdir = None
            else:
                log.warning("tmux_not_available")

        _check_auth()

        cmd, env, temp_files = self.build_command(request)

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

            usage = _parse_codex_ndjson_usage(result.stdout)

            return AgentRunResult(
                stdout=result.stdout,
                return_code=result.return_code,
                usage=usage,
                metadata=result.metadata,
            )
        finally:
            backup = getattr(self, "_agents_md_backup", None)
            for f in temp_files:
                if f.name == "AGENTS.md":
                    _cleanup_agents_md(f, backup)
                else:
                    f.unlink(missing_ok=True)
            self._agents_md_backup = None
            if hasattr(self, "_tmpdir") and self._tmpdir is not None:
                self._tmpdir.cleanup()
                self._tmpdir = None

    def build_interactive_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the CLI command, env dict, and temp files for an interactive invocation."""
        temp_files: list[Path] = []

        project_root = request.project_path or request.cwd
        agents_md, self._agents_md_backup = _write_agents_md(project_root, request.prompt)
        temp_files.append(agents_md)

        cmd = ["codex", request.task]

        if _using_api_key():
            cmd.append("--ignore-user-config")

        if request.skip_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")

        if request.model:
            cmd.extend(["--model", request.model])

        env, tmpdir = _make_codex_env()
        self._tmpdir = tmpdir
        return cmd, env, temp_files

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Codex CLI session as a subprocess."""
        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth()

        cmd, env, temp_files = self.build_interactive_command(request)
        try:
            log.info("codex_interactive", cwd=str(request.cwd))
            result = subprocess.run(cmd, cwd=request.cwd, env=env)
            return result.returncode
        finally:
            backup = getattr(self, "_agents_md_backup", None)
            for f in temp_files:
                if f.name == "AGENTS.md":
                    _cleanup_agents_md(f, backup)
                else:
                    f.unlink(missing_ok=True)
            self._agents_md_backup = None
            if hasattr(self, "_tmpdir") and self._tmpdir is not None:
                self._tmpdir.cleanup()
                self._tmpdir = None

