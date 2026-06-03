"""BobRunner — Bob Shell CLI backend implementation."""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from factory.runners.abstraction import (
    AgentRunner,
    Request,
    Response,
    RunnerIdentity,
)
from factory.runners.usage import (
    CeilingExceededError,
    check_ceilings,
    log_usage,
)

logger = logging.getLogger(__name__)

_auth_checked = False

# File where we persist the API key for nested subagent spawns
_AUTH_FILE_NAME = ".bob_auth"

_IDENTITY = RunnerIdentity(
    name="bob",
    display_name="Bob Shell",
    binary="bob",
    capabilities=set(),
)

# Bob Shell only supports built-in modes: plan, code, advanced, ask.
# We use 'code' mode for agent work; the role is injected via the prompt.
_BOB_CHAT_MODE = "code"


class BobAuthError(Exception):
    """Raised when BOBSHELL_API_KEY is not set."""

    def __init__(self) -> None:
        super().__init__(
            "BOBSHELL_API_KEY environment variable is not set. "
            "See bob-runner-package/bob-shell-docs/README.md for setup instructions."
        )


def _find_auth_file(start_path: Path) -> Path | None:
    """Search for the auth file starting from start_path and walking up.

    Returns the path to the auth file if found, or None.
    """
    path = start_path.resolve()
    while path != path.parent:
        auth_file = path / ".factory" / _AUTH_FILE_NAME
        if auth_file.is_file():
            return auth_file
        path = path.parent
    return None


def _persist_key(project_path: Path) -> None:
    """Persist BOBSHELL_API_KEY to a file for nested subagent spawns.

    Only writes if:
    - BOBSHELL_API_KEY is set in the environment
    - The .factory directory exists
    - The file doesn't already exist (or we're updating it)

    The file is created with chmod 600 for security.
    """
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
    """Check that BOBSHELL_API_KEY is set (once per process).

    Resolution order:
    1. Environment variable BOBSHELL_API_KEY
    2. File .factory/.bob_auth (searched from start_path upward, or cwd if None)

    If found in file, injects into os.environ for subprocess inheritance.

    Limitation: _auth_checked is a module-level flag, so the check only runs once
    per Python process. If the key changes or expires mid-session, the cached
    result won't reflect that. For long-running processes, consider restarting
    the factory rather than relying on key rotation.
    """
    global _auth_checked
    if _auth_checked:
        return

    # First check environment variable
    if os.environ.get("BOBSHELL_API_KEY"):
        _auth_checked = True
        return

    # Fall back to file-based persistence
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
    """Find the directory containing the bob binary.

    Used to prepend to PATH so that the correct Node version is found
    when bob's shebang resolves `#!/usr/bin/env node`.
    """
    bob_path = shutil.which("bob")
    if bob_path:
        return str(Path(bob_path).parent)
    return None


def _make_env_with_bob_path() -> dict[str, str]:
    """Create environment dict with bob's bin directory prepended to PATH.

    Bob Shell's shebang is `#!/usr/bin/env node`. If multiple Node version
    managers (nvm, fnm, volta) are installed, the wrong Node may be found.
    Prepending bob's bin directory ensures its companion node is used.
    """
    env = dict(os.environ)
    bob_bin_dir = _get_bob_bin_dir()
    if bob_bin_dir:
        current_path = env.get("PATH", "")
        if not current_path.startswith(bob_bin_dir):
            env["PATH"] = f"{bob_bin_dir}:{current_path}"
            logger.debug("Prepended bob bin dir to PATH: %s", bob_bin_dir)
    return env


def _sanitize_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    import re

    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


class BobRunner(AgentRunner):
    """Runner implementation for Bob Shell CLI."""

    name: str = "bob"

    def __init__(
        self,
        cycle_start: datetime | None = None,
        project_path: Path | None = None,
    ) -> None:
        """Initialize BobRunner.

        Args:
            cycle_start: Start time of the current factory cycle (for ceiling tracking).
                If not provided and project_path is given, reads from cycle.json.
            project_path: Path to the project (used to read cycle state from cycle.json).
        """
        if cycle_start is not None:
            self.cycle_start = cycle_start
        elif project_path is not None:
            from factory.ceo_completion import read_cycle_state

            state = read_cycle_state(project_path)
            self.cycle_start = state.started_at if state else datetime.now(timezone.utc)
        else:
            self.cycle_start = datetime.now(timezone.utc)

    @property
    def identity(self) -> RunnerIdentity:
        return _IDENTITY

    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        cmd = ["bob", "-p", request.prompt, f"--chat-mode={_BOB_CHAT_MODE}"]
        if request.skip_permissions:
            cmd.append("--yolo")
        return cmd

    def _build_env(self, request: Request) -> dict[str, str]:
        env = _make_env_with_bob_path()
        # Strip VIRTUAL_ENV like the base class
        env.pop("VIRTUAL_ENV", None)
        if request.env:
            env.update(request.env)
        # Persist key for nested subagent spawns
        project_path = self._find_project_path(Path(request.cwd))
        _persist_key(project_path)
        return env

    def _parse_response(
        self, stdout: str, stderr: str, exit_code: int
    ) -> Response:
        return Response(output=_sanitize_ansi(stdout), exit_code=exit_code)

    async def check_health(self) -> tuple[bool, str]:
        """Check bob binary and BOBSHELL_API_KEY."""
        ok, msg = await super().check_health()
        if not ok:
            return ok, msg
        if os.environ.get("BOBSHELL_API_KEY"):
            return True, f"{self.identity.display_name} found and API key set"
        try:
            _check_auth()
            return True, f"{self.identity.display_name} found and API key set"
        except BobAuthError:
            return False, "BOBSHELL_API_KEY not set"

    async def run(self, request: Request) -> Response:
        """Override to add dry-run detection, ceiling enforcement, usage logging."""
        project_path = self._find_project_path(Path(request.cwd))

        # Persist key before dry-run check so file exists for nested spawns
        _persist_key(project_path)

        if is_dry_run():
            stdout, code = self._dry_run_response(request.role, Path(request.cwd), request.task)
            return Response(output=stdout, exit_code=code)

        _check_auth(Path(request.cwd))

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            self._emit_ceiling_event(project_path, e)
            return Response(output=str(e), exit_code=1, error=str(e))

        start_time = time.monotonic()
        response = await super().run(request)
        duration = time.monotonic() - start_time

        log_usage(
            project_path, request.role, Path(request.cwd),
            duration, response.exit_code, dry_run=False,
        )

        return response

    def run_interactive(self, request: Request) -> int:
        """Override for interactive Bob Shell session."""
        import subprocess as _subprocess

        project_path = self._find_project_path(Path(request.cwd))
        _persist_key(project_path)

        if is_dry_run():
            yolo_flag = " --yolo" if request.skip_permissions else ""
            print(f"[DRY-RUN] Would run: bob --chat-mode={_BOB_CHAT_MODE}{yolo_flag}")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth(Path(request.cwd))

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            print(f"ERROR: {e}")
            return 1

        cmd = [
            "bob",
            f"--chat-mode={_BOB_CHAT_MODE}",
            "-i", request.prompt,
        ]
        if request.skip_permissions:
            cmd.append("--yolo")

        logger.info("BobRunner interactive: cwd=%s", request.cwd)

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
