"""BobRunner — Bob Shell CLI backend implementation."""

from __future__ import annotations

import os
import shutil
import subprocess as _subprocess
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

_AUTH_FILE_NAME = ".bob_auth"


class BobAuthError(Exception):
    """Raised when BOBSHELL_API_KEY is not set."""

    def __init__(self) -> None:
        super().__init__(
            "BOBSHELL_API_KEY environment variable is not set. "
            "See bob-runner-package/bob-shell-docs/README.md for setup instructions."
        )


def _find_auth_file(start_path: Path) -> Path | None:
    """Search for the auth file starting from start_path and walking up."""
    path = start_path.resolve()
    while path != path.parent:
        auth_file = path / ".factory" / _AUTH_FILE_NAME
        if auth_file.is_file():
            return auth_file
        path = path.parent
    return None


def _persist_key(project_path: Path) -> None:
    """Persist BOBSHELL_API_KEY to a file for nested subagent spawns."""
    key = os.environ.get("BOBSHELL_API_KEY")
    if not key:
        return

    factory_dir = project_path / ".factory"
    if not factory_dir.is_dir():
        return

    auth_file = factory_dir / _AUTH_FILE_NAME
    try:
        auth_file.write_text(key)
        auth_file.chmod(0o600)
        log.debug("bob_key_persisted", path=str(auth_file))
    except OSError as e:
        log.warning("bob_key_persist_failed", error=str(e))


def _check_auth(start_path: Path | None = None) -> None:
    """Check that BOBSHELL_API_KEY is set (once per process)."""
    global _auth_checked
    if _auth_checked:
        return

    if os.environ.get("BOBSHELL_API_KEY"):
        _auth_checked = True
        return

    search_from = start_path if start_path is not None else Path.cwd()
    auth_file = _find_auth_file(search_from)
    if auth_file:
        try:
            key = auth_file.read_text().strip()
            if key:
                os.environ["BOBSHELL_API_KEY"] = key
                log.info("bob_key_loaded", path=str(auth_file))
                _auth_checked = True
                return
        except OSError as e:
            log.warning("bob_auth_file_read_failed", path=str(auth_file), error=str(e))

    bob_config = Path.home() / ".bob" / "settings.json"
    if bob_config.is_file():
        log.info("bob_native_auth_detected", path=str(bob_config))
        _auth_checked = True
        return

    raise BobAuthError()


def is_dry_run() -> bool:
    """Return True if dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("bob_dry_run", env_var="FACTORY_BOB_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


def _get_bob_bin_dir() -> str | None:
    """Find the directory containing the bob binary."""
    bob_path = shutil.which("bob")
    if bob_path:
        return str(Path(bob_path).parent)
    return None


def _make_env_with_bob_path() -> dict[str, str]:
    """Create environment dict with bob's bin directory prepended to PATH."""
    env = dict(os.environ)
    bob_bin_dir = _get_bob_bin_dir()
    if bob_bin_dir:
        current_path = env.get("PATH", "")
        if not current_path.startswith(bob_bin_dir):
            env["PATH"] = f"{bob_bin_dir}:{current_path}"
            log.debug("bob_path_prepended", dir=bob_bin_dir)
    return env


_BOB_CHAT_MODE = "code"


class BobRunner:
    """Runner implementation for Bob Shell CLI."""

    name: str = "bob"

    @classmethod
    def metadata(cls) -> RunnerMeta:
        from factory.runners.protocol import RunnerMeta
        return RunnerMeta(
            name="bob",
            display_name="Bob Shell",
            binary="bob",
            install_hint="npm install -g bob-shell",
            required_env_vars=["BOBSHELL_API_KEY"],
            supports_model_override=False,
            supports_usage_telemetry=False,
            supports_session_name=False,
        )

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

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the Bob Shell CLI command and env dict."""
        chat_mode = _BOB_CHAT_MODE
        full_task = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = ["bob", "-p", full_task, f"--chat-mode={chat_mode}"]
        if request.skip_permissions:
            cmd.append("--yolo")

        env = _make_env_with_bob_path()
        return cmd, env, []

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Bob Shell invocation."""
        from factory.models import AgentRunResult

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            log.warning("bob_tmux_not_supported")
        self._role = request.role
        project_path = request.project_path or self._find_project_path(request.cwd)

        _persist_key(project_path)

        if is_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            result = make_dry_run_result("bob", request.role, request.cwd, request.task)
            log_usage(project_path, request.role, request.cwd, 0.0, 0, dry_run=True)
            return result

        _check_auth(request.cwd)

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            self._emit_ceiling_event(project_path, e)
            return AgentRunResult(stdout=str(e), return_code=1)

        cmd, env, _ = self.build_command(request)

        log.info("bob_headless", cwd=str(request.cwd), role=request.role, chat_mode=_BOB_CHAT_MODE)

        start_time = time.monotonic()

        result = await run_subprocess(
            cmd, cwd=str(request.cwd), env=env,
            timeout=request.timeout, runner_name="bob", role=request.role,
            sanitize=True,
        )

        duration = time.monotonic() - start_time
        log_usage(project_path, request.role, request.cwd, duration, result.return_code, dry_run=False)

        return result

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Bob Shell session as a subprocess."""
        project_path = request.project_path or self._find_project_path(request.cwd)

        _persist_key(project_path)

        if is_dry_run():
            yolo_flag = " --yolo" if request.skip_permissions else ""
            print(f"[DRY-RUN] Would run: bob --chat-mode=factory-{request.role}{yolo_flag}")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth(request.cwd)

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            print(f"ERROR: {e}")
            return 1

        chat_mode = _BOB_CHAT_MODE
        full_task = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = [
            "bob",
            f"--chat-mode={chat_mode}",
            "-i", full_task,
        ]
        if request.skip_permissions:
            cmd.append("--yolo")

        log.info("bob_interactive", cwd=str(request.cwd), chat_mode=chat_mode)

        bob_bin_dir = _get_bob_bin_dir()
        if bob_bin_dir and not os.environ.get("PATH", "").startswith(bob_bin_dir):
            os.environ["PATH"] = f"{bob_bin_dir}:{os.environ.get('PATH', '')}"

        result = _subprocess.run(cmd, cwd=request.cwd)
        return result.returncode

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
                "bob.ceiling_exceeded",
                data={
                    "ceiling": error.ceiling_name,
                    "current": error.current,
                    "limit": error.limit,
                    "env_var": error.env_var,
                },
            )
        except Exception:
            log.debug("bob_ceiling_event_failed", exc_info=True)
