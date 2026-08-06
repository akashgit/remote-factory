"""OpenCodeRunner — OpenCode v1.x (anomalyco/opencode) CLI backend."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess
from factory.runners.usage import (
    CeilingExceededError,
    check_ceilings,
    log_usage,
)

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult
    from factory.runners.protocol import RunnerMeta

log = structlog.get_logger()

_auth_checked = False
_compat_checked = False

_RUNNER_NAME = "opencode"

_PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_OPENAI_API_KEY",
)


class OpenCodeAuthError(Exception):
    """Raised when no OpenCode auth is available."""

    def __init__(self) -> None:
        super().__init__(
            "No OpenCode authentication found. "
            "Run 'opencode auth login' to authenticate, "
            "or set a provider API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.). "
            "Alternatively, add keys to a config.toml credential profile: "
            "[credentials.opencode] ANTHROPIC_API_KEY = \"...\""
        )


def _has_opencode_auth() -> bool:
    """Check if OpenCode auth is available via config dir or provider env vars."""
    opencode_dir = Path.home() / ".opencode"
    if opencode_dir.is_dir():
        return True
    return any(os.environ.get(v) for v in _PROVIDER_ENV_VARS)


def _check_auth() -> None:
    """Check that OpenCode auth is available (once per process)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    _check_binary_compat()
    if _has_opencode_auth():
        _auth_checked = True
        return
    raise OpenCodeAuthError()


def _check_binary_compat() -> None:
    """Warn if the opencode binary is v0.x (archived Go version).

    v1.x (anomalyco/opencode) outputs version strings like "1.18.14".
    v0.x (opencode-ai/opencode) outputs "opencode version v0.x.x".
    """
    global _compat_checked  # noqa: PLW0603
    if _compat_checked:
        return
    _compat_checked = True

    import re
    import shutil

    if not shutil.which("opencode"):
        return

    try:
        result = subprocess.run(
            ["opencode", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (getattr(result, "stdout", None) or "").strip() + (getattr(result, "stderr", None) or "").strip()
        if re.search(r"\bv?0\.\d+\.\d+", output):
            log.warning(
                "opencode_binary_v0x_detected",
                output=output,
                hint=(
                    "The opencode binary appears to be v0.x (archived). "
                    "The factory requires OpenCode v1.x (anomalyco/opencode). "
                    "Install via: curl -fsSL https://opencode.ai/install | bash "
                    "or: npm i -g opencode-ai"
                ),
            )
            return
        log.debug("opencode_binary_compat_ok", output=output)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        log.debug("opencode_version_check_timeout")


def _parse_opencode_output(raw: str) -> tuple[str, str | None]:
    """Try to parse --format json output from OpenCode v1.x.

    Returns (text, session_id). Falls back to (raw, None) if not parseable.
    """
    for line in reversed(raw.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            text = data.get("content", data.get("text", data.get("message", "")))
            session_id = data.get("sessionId", data.get("session_id"))
            if text:
                return str(text), session_id
        except (json.JSONDecodeError, AttributeError):
            continue
    return raw, None


def is_opencode_dry_run() -> bool:
    """Return True if OpenCode dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("opencode_dry_run", env_var="FACTORY_OPENCODE_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class OpenCodeRunner:
    """Runner implementation for OpenCode v1.x CLI (anomalyco/opencode)."""

    name: str = "opencode"

    def __init__(
        self,
        cycle_start: datetime | None = None,
        project_path: Path | None = None,
    ) -> None:
        if cycle_start is not None:
            self.cycle_start = cycle_start
        elif project_path is not None:
            from factory.ceo_completion import read_cycle_state

            state = read_cycle_state(project_path)
            self.cycle_start = state.started_at if state else datetime.now(timezone.utc)
        else:
            self.cycle_start = datetime.now(timezone.utc)
        self._role: str = "unknown"

    @classmethod
    def metadata(cls) -> RunnerMeta:
        from factory.runners.protocol import RunnerMeta
        return RunnerMeta(
            name="opencode",
            display_name="OpenCode",
            binary="opencode",
            install_hint="curl -fsSL https://opencode.ai/install | bash",
            required_env_vars=[],
            supports_model_override=True,
            supports_interactive=True,
            supports_streaming=True,
            supports_usage_telemetry=False,
            supports_session_name=True,
            supports_session_resume=True,
            supports_background=False,
            custom_auth_check=_has_opencode_auth,
        )

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the OpenCode v1.x CLI command for headless execution."""
        cwd = Path(request.cwd)
        agents_md_path = cwd / "AGENTS.md"
        agents_md_path.write_text(request.prompt)
        temp_files: list[Path] = [agents_md_path]

        cmd = ["opencode", "run", request.task, "--format", "json", "--dir", str(request.cwd)]

        if request.skip_permissions:
            cmd.append("--auto")

        if request.model:
            cmd.extend(["--model", request.model])

        if request.session_name:
            cmd.extend(["--title", request.session_name])

        if request.resume_session_id:
            cmd.extend(["--session", request.resume_session_id])

        if request.session_id:
            cmd.append("--continue")

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        return cmd, env, temp_files

    def build_interactive_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the CLI command for interactive (TUI) mode."""
        cwd = Path(request.cwd)
        agents_md_path = cwd / "AGENTS.md"
        agents_md_path.write_text(request.prompt)
        temp_files: list[Path] = [agents_md_path]

        cmd = ["opencode", "--prompt", request.task, str(request.cwd)]

        if request.model:
            cmd.extend(["--model", request.model])

        if request.resume_session_id:
            cmd.extend(["--session", request.resume_session_id])

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        return cmd, env, temp_files

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless OpenCode v1.x invocation."""
        from factory.models import AgentRunResult

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            return AgentRunResult(
                stdout="Error: --tmux-persist is not supported with the opencode runner. Use --runner claude.",
                return_code=1,
            )

        background = request.extras.get("background", False)
        if background:
            return AgentRunResult(
                stdout="Error: --bg is not supported with the opencode runner. Use --runner claude.",
                return_code=1,
            )

        self._role = request.role
        project_path = request.project_path or self._find_project_path(request.cwd)

        if is_opencode_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            result = make_dry_run_result("opencode", request.role, request.cwd, request.task)
            log_usage(project_path, request.role, request.cwd, 0.0, 0, dry_run=True, runner_name=_RUNNER_NAME)
            return result

        _check_auth()

        try:
            check_ceilings(project_path, self.cycle_start, runner_name=_RUNNER_NAME)
        except CeilingExceededError as e:
            self._emit_ceiling_event(project_path, e)
            return AgentRunResult(stdout=str(e), return_code=1)

        cmd, env, temp_files = self.build_command(request)

        log.info("opencode_headless", cwd=str(request.cwd), role=request.role, model=request.model)

        start_time = time.monotonic()

        try:
            result = await run_subprocess(
                cmd, cwd=str(request.cwd), env=env,
                timeout=request.timeout, runner_name="opencode", role=request.role,
                sanitize=True,
            )

            duration = time.monotonic() - start_time
            log_usage(project_path, request.role, request.cwd, duration, result.return_code, dry_run=False, runner_name=_RUNNER_NAME)

            return result
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive OpenCode v1.x session as a subprocess."""
        project_path = request.project_path or self._find_project_path(request.cwd)

        if is_opencode_dry_run():
            print("[DRY-RUN] Would exec: opencode (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth()

        try:
            check_ceilings(project_path, self.cycle_start, runner_name=_RUNNER_NAME)
        except CeilingExceededError as e:
            print(f"ERROR: {e}")
            return 1

        cmd, env, temp_files = self.build_interactive_command(request)

        log.info("opencode_interactive", cwd=str(request.cwd))

        try:
            result = subprocess.run(cmd, cwd=request.cwd, env=env)
            return result.returncode
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def _find_project_path(self, cwd: Path) -> Path:
        """Find the project root (directory containing .factory/)."""
        path = cwd.resolve()
        while path != path.parent:
            if (path / ".factory").is_dir():
                return path
            path = path.parent
        return cwd.resolve()

    def _emit_ceiling_event(self, project_path: Path, error: CeilingExceededError) -> None:
        """Emit a structured event when a ceiling is hit."""
        try:
            from factory.events import emit_event

            emit_event(
                project_path,
                "opencode.ceiling_exceeded",
                data={
                    "ceiling": error.ceiling_name,
                    "current": error.current,
                    "limit": error.limit,
                    "env_var": error.env_var,
                },
            )
        except Exception:
            log.debug("opencode_ceiling_event_failed", exc_info=True)
