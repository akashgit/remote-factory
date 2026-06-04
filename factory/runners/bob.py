"""BobRunner — Bob Shell CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess as _subprocess

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.config import AgentLaunchConfig
from factory.runners.protocol import AgentResult
from factory.runners.usage import (
    CeilingExceededError,
    check_ceilings,
    log_usage,
)

logger = logging.getLogger(__name__)

_auth_checked = False

# File where we persist the API key for nested subagent spawns
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
        logger.debug("Persisted BOBSHELL_API_KEY to %s", auth_file)
    except OSError as e:
        logger.warning("Failed to persist API key: %s", e)


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
                logger.info("Loaded BOBSHELL_API_KEY from %s", auth_file)
                _auth_checked = True
                return
        except OSError as e:
            logger.warning("Failed to read auth file %s: %s", auth_file, e)

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
            logger.debug("Prepended bob bin dir to PATH: %s", bob_bin_dir)
    return env


# Bob Shell only supports built-in modes: plan, code, advanced, ask.
_BOB_CHAT_MODE = "code"


def _find_project_path(cwd: Path) -> Path:
    """Find the project root (directory containing .factory/)."""
    path = cwd.resolve()
    while path != path.parent:
        if (path / ".factory").is_dir():
            return path
        path = path.parent
    return cwd.resolve()


class BobShellAgent:
    """Agent implementation for Bob Shell CLI (pure command building)."""

    name: str = "bob"

    def __init__(self, project_path: Path | None = None) -> None:
        self._project_path = project_path

    def get_launch_command(self, config: AgentLaunchConfig) -> list[str]:
        """Build the bob CLI command."""
        full_task = f"{config.prompt}\n\n---\n\n## Current Task\n\n{config.task}"
        cmd = ["bob", "-p", full_task, f"--chat-mode={_BOB_CHAT_MODE}"]
        if config.permissions == "permissionless":
            cmd.append("--yolo")
        return cmd

    def get_environment(self, config: AgentLaunchConfig) -> dict[str, str]:
        """Build subprocess environment for Bob Shell."""
        return _make_env_with_bob_path()

    def parse_output(self, stdout: str, return_code: int) -> AgentResult:
        """Parse Bob Shell output (raw text, no usage telemetry)."""
        return AgentResult(output=stdout, return_code=return_code, usage=None)

    def preflight(self) -> None:
        """Check auth and dry-run mode, persist key, check ceilings."""
        project_path = self._project_path or Path.cwd()
        _persist_key(project_path)
        if is_dry_run():
            return
        _check_auth(project_path)


class BobRunner:
    """Runner implementation for Bob Shell CLI."""

    name: str = "bob"

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
        """Run a headless Bob Shell invocation."""
        _ = session_name
        if tmux_persist:
            logger.warning("tmux_persist not supported with bob runner")
        self._role = role
        project_path = self._find_project_path(cwd)

        _persist_key(project_path)

        if is_dry_run():
            stdout, code = self._dry_run_response(role, cwd, task)
            return stdout, code, None

        _check_auth(cwd)

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            self._emit_ceiling_event(project_path, e)
            return str(e), 1, None

        chat_mode = _BOB_CHAT_MODE
        full_task = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = ["bob", "-p", full_task, f"--chat-mode={chat_mode}"]
        if dangerously_skip_permissions:
            cmd.append("--yolo")

        logger.info("BobRunner headless: cwd=%s, role=%s, chat_mode=%s", cwd, role, chat_mode)

        env = _make_env_with_bob_path()
        start_time = time.monotonic()

        stream = should_stream()
        prefix = f"[bob:{role}]" if stream else None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                stream_subprocess(proc, stream=stream, prefix=prefix, sanitize=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            duration = time.monotonic() - start_time
            log_usage(project_path, role, cwd, duration, 1, dry_run=False)
            logger.error("BobRunner timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1, None
        except FileNotFoundError:
            logger.error("'bob' CLI not found on PATH")
            return "Error: 'bob' CLI not found on PATH", 1, None

        duration = time.monotonic() - start_time
        return_code = proc.returncode or 0

        log_usage(project_path, role, cwd, duration, return_code, dry_run=False)

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if return_code != 0:
            logger.warning("BobRunner exited with code %d: %s", return_code, stderr[:200])

        return stdout, return_code, None

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
        """Run an interactive Bob Shell session as a subprocess."""
        _ = session_name
        project_path = self._find_project_path(cwd)

        _persist_key(project_path)

        if is_dry_run():
            yolo_flag = " --yolo" if dangerously_skip_permissions else ""
            print(f"[DRY-RUN] Would run: bob --chat-mode=factory-{role}{yolo_flag}")
            print(f"[DRY-RUN] Task: {task[:200]}...")
            return 0

        _check_auth(cwd)

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            print(f"ERROR: {e}")
            return 1

        chat_mode = _BOB_CHAT_MODE
        full_task = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = [
            "bob",
            f"--chat-mode={chat_mode}",
            "-i", full_task,
        ]
        if dangerously_skip_permissions:
            cmd.append("--yolo")

        logger.info("BobRunner interactive_run: cwd=%s, chat_mode=%s", cwd, chat_mode)

        bob_bin_dir = _get_bob_bin_dir()
        if bob_bin_dir and not os.environ.get("PATH", "").startswith(bob_bin_dir):
            os.environ["PATH"] = f"{bob_bin_dir}:{os.environ.get('PATH', '')}"

        result = _subprocess.run(cmd, cwd=cwd)
        return result.returncode

    def _find_project_path(self, cwd: Path) -> Path:
        """Find the project root (directory containing .factory/)."""
        return _find_project_path(cwd)

    def _dry_run_response(self, role: str, cwd: Path, task: str) -> tuple[str, int]:
        """Return a stub response for dry-run mode."""
        project_path = self._find_project_path(cwd)

        log_usage(project_path, role, cwd, 0.0, 0, dry_run=True)

        response = (
            f"[DRY-RUN] BobRunner would have executed:\n"
            f"  role: {role}\n"
            f"  cwd: {cwd}\n"
            f"  task: {task[:100]}...\n"
            f"\n"
            f"Dry-run stub response: Task acknowledged."
        )
        logger.info("BobRunner dry-run: role=%s, cwd=%s", role, cwd)
        return response, 0

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
            logger.debug("Failed to emit ceiling event", exc_info=True)
