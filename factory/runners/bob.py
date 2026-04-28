"""BobRunner — Bob Shell CLI backend implementation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import yaml

from factory.runners._stream import should_stream, stream_subprocess
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


def _check_auth() -> None:
    """Check that BOBSHELL_API_KEY is set (once per process).

    Resolution order:
    1. Environment variable BOBSHELL_API_KEY
    2. File .factory/.bob_auth (searched from cwd upward)

    If found in file, injects into os.environ for subprocess inheritance.
    """
    global _auth_checked
    if _auth_checked:
        return

    # First check environment variable
    if os.environ.get("BOBSHELL_API_KEY"):
        _auth_checked = True
        return

    # Fall back to file-based persistence
    auth_file = _find_auth_file(Path.cwd())
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
    return os.environ.get("FACTORY_BOB_DRY_RUN", "").lower() in ("1", "true", "yes")


def _prompt_hash(prompt: str) -> str:
    """Compute a short hash of the prompt for cache invalidation."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _ensure_custom_modes(project_path: Path, role: str, prompt: str) -> None:
    """Ensure the factory custom mode exists in .factory/.bob/custom_modes.yaml.

    Creates the mode for the given role if it doesn't exist.
    Updates the mode if the prompt has changed (detected via hash).
    """
    # NOTE: Bob Shell limits roleDefinition to 2000 chars; large prompts are truncated
    bob_dir = project_path / ".factory" / ".bob"
    bob_dir.mkdir(parents=True, exist_ok=True)

    modes_file = bob_dir / "custom_modes.yaml"

    existing_modes: dict = {}
    if modes_file.exists():
        try:
            existing_modes = yaml.safe_load(modes_file.read_text()) or {}
        except yaml.YAMLError:
            existing_modes = {}

    custom_modes = existing_modes.get("customModes", [])

    mode_slug = f"factory-{role}"
    current_hash = _prompt_hash(prompt)

    # Find existing mode if any
    existing_mode_idx = None
    for idx, m in enumerate(custom_modes):
        if m.get("slug") == mode_slug:
            existing_mode_idx = idx
            break

    if existing_mode_idx is not None:
        existing_hash = custom_modes[existing_mode_idx].get("_promptHash")
        if existing_hash == current_hash:
            return  # Cache hit — prompt unchanged
        # Cache miss — prompt changed, update the mode
        logger.info("Prompt changed for %s (hash %s → %s), updating mode", role, existing_hash, current_hash)
        custom_modes.pop(existing_mode_idx)

    new_mode = {
        "slug": mode_slug,
        "name": f"Factory {role.title()}",
        "roleDefinition": prompt[:2000],
        "whenToUse": f"Factory agent role: {role}",
        "groups": ["read", "command", "edit"],
        "_promptHash": current_hash,
    }
    custom_modes.append(new_mode)
    existing_modes["customModes"] = custom_modes

    modes_file.write_text(yaml.dump(existing_modes, default_flow_style=False, allow_unicode=True))
    logger.info("Wrote bob custom mode: %s (hash %s) to %s", mode_slug, current_hash, modes_file)


def _get_chat_mode(role: str, model: str | None) -> str:
    """Determine the chat mode to use.

    Always returns the role-based mode (factory-<role>).
    The model parameter is accepted for API compatibility but not used.
    """
    return f"factory-{role}"


class BobRunner:
    """Runner implementation for Bob Shell CLI."""

    name: str = "bob"

    def __init__(self, cycle_start: datetime | None = None) -> None:
        """Initialize BobRunner.

        Args:
            cycle_start: Start time of the current factory cycle (for ceiling tracking).
        """
        self.cycle_start = cycle_start or datetime.now(timezone.utc)
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
    ) -> tuple[str, int]:
        """Run a headless Bob Shell invocation.

        Returns (stdout, return_code).
        """
        self._role = role
        project_path = self._find_project_path(cwd)

        # Persist key for nested subagent spawns (before dry-run check so file exists)
        _persist_key(project_path)

        if is_dry_run():
            return self._dry_run_response(role, cwd, task)

        _check_auth()

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            self._emit_ceiling_event(project_path, e)
            return str(e), 1

        _ensure_custom_modes(project_path, role, prompt)

        chat_mode = _get_chat_mode(role, model)
        full_task = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = ["bob", "-p", full_task, f"--chat-mode={chat_mode}"]
        if dangerously_skip_permissions:
            cmd.append("--yolo")

        logger.info("BobRunner headless: cwd=%s, role=%s, chat_mode=%s", cwd, role, chat_mode)

        env = dict(os.environ)
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
                stream_subprocess(proc, stream=stream, prefix=prefix),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            duration = time.monotonic() - start_time
            log_usage(project_path, role, cwd, duration, 1, dry_run=False)
            logger.error("BobRunner timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("'bob' CLI not found on PATH")
            return "Error: 'bob' CLI not found on PATH", 1

        duration = time.monotonic() - start_time
        return_code = proc.returncode or 0

        log_usage(project_path, role, cwd, duration, return_code, dry_run=False)

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if return_code != 0:
            logger.warning("BobRunner exited with code %d: %s", return_code, stderr[:200])

        return stdout, return_code

    def interactive_exec(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        model: str | None = None,
        role: str = "ceo",
        dangerously_skip_permissions: bool = False,
    ) -> NoReturn:
        """Replace process with interactive Bob Shell session."""
        project_path = self._find_project_path(cwd)

        # Persist key for nested subagent spawns (before dry-run check so file exists)
        _persist_key(project_path)

        if is_dry_run():
            yolo_flag = " --yolo" if dangerously_skip_permissions else ""
            print(f"[DRY-RUN] Would exec: bob --chat-mode=factory-{role}{yolo_flag}")
            print(f"[DRY-RUN] Task: {task[:200]}...")
            raise SystemExit(0)

        _check_auth()

        try:
            check_ceilings(project_path, self.cycle_start)
        except CeilingExceededError as e:
            print(f"ERROR: {e}")
            raise SystemExit(1) from e

        _ensure_custom_modes(project_path, role, prompt)

        chat_mode = _get_chat_mode(role, model)

        cmd = [
            "bob",
            f"--chat-mode={chat_mode}",
            "-i", task,
        ]
        if dangerously_skip_permissions:
            cmd.append("--yolo")

        logger.info("BobRunner interactive_exec: cwd=%s, chat_mode=%s", cwd, chat_mode)

        os.chdir(cwd)
        os.execvp("bob", cmd)

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


