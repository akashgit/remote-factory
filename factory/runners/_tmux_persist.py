"""Tmux persist — launch agents interactively in tmux with output capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_PREFIX = "factory-persist-"
_SENTINEL_POLL_INITIAL = 0.1
_SENTINEL_POLL_CAP = 2.0
_EXITCODE_POLL_INTERVAL = 0.1
_EXITCODE_POLL_TIMEOUT = 3.0
_WINDOW_POLL_INTERVAL = 0.1
_WINDOW_POLL_TIMEOUT = 3.0


def find_project_path(cwd: Path) -> Path:
    """Find the project root by walking up from cwd looking for .factory/."""
    path = cwd.resolve()
    while path != path.parent:
        if (path / ".factory").is_dir():
            return path
        path = path.parent
    return cwd.resolve()


def tmux_available() -> bool:
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


_ANSI_RE = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07|[78=>])")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _session_exists(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    ).returncode == 0


def _window_exists(session: str, window: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", f"{session}:{window}"],
        capture_output=True,
    ).returncode == 0


_DEFAULT_TMUX_TIMEOUT = 86400.0  # 24 hours — interactive sessions are user-driven


def _generate_settings(sentinel_path: Path, tmpdir: Path, project_path: Path) -> Path:
    """Generate a settings.json with Stop/StopFailure hooks merged with existing project settings."""
    factory_hooks = {
        "Stop": [
            {"hooks": [{"type": "command", "command": f"touch {shlex.quote(str(sentinel_path))}", "timeout": 5}]}
        ],
        "StopFailure": [
            {"hooks": [{"type": "command", "command": f"touch {shlex.quote(str(sentinel_path))}", "timeout": 5}]}
        ],
    }

    settings: dict = {}
    existing_settings_path = project_path / ".claude" / "settings.json"
    if existing_settings_path.exists():
        try:
            settings = json.loads(existing_settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing_hooks = settings.get("hooks", {})
    for hook_name, hook_entries in factory_hooks.items():
        existing_hooks[hook_name] = existing_hooks.get(hook_name, []) + hook_entries
    settings["hooks"] = existing_hooks

    settings_file = tmpdir / "settings.json"
    settings_file.write_text(json.dumps(settings))
    return settings_file


async def _wait_for_sentinel(sentinel_path: Path, timeout: float) -> bool:
    """Poll for sentinel file creation with exponential backoff. Returns True if found, False on timeout."""
    deadline = time.monotonic() + timeout
    interval = _SENTINEL_POLL_INITIAL
    while time.monotonic() < deadline:
        if sentinel_path.exists():
            return True
        await asyncio.sleep(max(0, min(interval, deadline - time.monotonic())))
        interval = min(interval * 2, _SENTINEL_POLL_CAP)
    return sentinel_path.exists()


async def _wait_for_exitcode(exitcode_file: Path) -> int:
    """Poll for exitcode file with short timeout. Returns exit code or 1 if not found."""
    deadline = time.monotonic() + _EXITCODE_POLL_TIMEOUT
    while time.monotonic() < deadline:
        if exitcode_file.exists():
            try:
                return int(exitcode_file.read_text().strip())
            except (ValueError, OSError):
                return 1
        await asyncio.sleep(_EXITCODE_POLL_INTERVAL)
    return 1


async def _wait_for_window_exit(session: str, window: str) -> None:
    """Poll for tmux window to disappear, up to _WINDOW_POLL_TIMEOUT."""
    deadline = time.monotonic() + _WINDOW_POLL_TIMEOUT
    while time.monotonic() < deadline:
        if not _window_exists(session, window):
            return
        await asyncio.sleep(_WINDOW_POLL_INTERVAL)


async def run_in_tmux(
    prompt: str,
    task: str,
    cwd: Path,
    role: str,
    project_path: Path,
    *,
    timeout: float = _DEFAULT_TMUX_TIMEOUT,
    model: str | None = None,
    dangerously_skip_permissions: bool = True,
) -> tuple[str, int, None]:
    """Launch claude interactively in a tmux window and wait for completion.

    Output is captured via the `script` command. Completion is signaled via
    a sentinel file touched by Claude Code Stop/StopFailure hooks. A trap
    handler provides crash-resilience for abnormal exits.

    Returns (stdout, return_code, None). Usage is always None for tmux mode.
    """
    run_id = uuid.uuid4().hex[:8]
    path_hash = hashlib.sha1(str(project_path).encode()).hexdigest()[:6]
    session = f"{_SESSION_PREFIX}{project_path.name}-{path_hash}"
    window = f"{role}-{run_id}"

    tmpdir = Path(tempfile.mkdtemp(prefix="factory-tmux-"))
    logfile = tmpdir / "output.log"
    exitcode_file = tmpdir / "exitcode"
    sentinel_file = tmpdir / "sentinel"
    wrapper_script = tmpdir / "wrapper.sh"

    prompt_file = tmpdir / "prompt.md"
    prompt_file.write_text(prompt)

    settings_file = _generate_settings(sentinel_file, tmpdir, project_path)

    cmd = ["claude", "--settings", str(settings_file), "--append-system-prompt-file", str(prompt_file)]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if model:
        cmd.extend(["--model", model])
    cmd.append(task)

    claude_cmd = shlex.join(cmd)
    logfile_q = shlex.quote(str(logfile))
    if platform.system() == "Darwin":
        script_line = f"script -q {logfile_q} {claude_cmd}\n"
    else:
        script_line = f"script -q -c {shlex.quote(claude_cmd)} {logfile_q}\n"

    sentinel_q = shlex.quote(str(sentinel_file))
    exitcode_q = shlex.quote(str(exitcode_file))
    wrapper_script.write_text(
        "#!/bin/bash\n"
        f"cleanup() {{ local rc=$?; echo $rc > {exitcode_q}; touch {sentinel_q}; }}\n"
        "trap cleanup EXIT\n"
        f"{script_line}"
    )
    wrapper_script.chmod(0o755)

    has_session = _session_exists(session)
    if has_session:
        result = subprocess.run(
            ["tmux", "new-window", "-t", session, "-n", window, str(wrapper_script)],
            cwd=cwd,
            capture_output=True,
        )
    else:
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-n", window,
             "-x", "200", "-y", "50", str(wrapper_script)],
            cwd=cwd,
            capture_output=True,
        )

    if result.returncode != 0:
        logger.warning("Failed to create tmux window for %s: %s", role, result.stderr.decode()[:200])
        _cleanup(tmpdir)
        return f"Failed to create tmux window for {role}", 1, None

    logger.info("tmux_launched session=%s window=%s role=%s", session, window, role)
    print(f"Agent '{role}' launched in tmux session: {session}", file=sys.stderr)
    print(f"  tmux attach -t {session}    # attach and interact", file=sys.stderr)
    print("  /exit or Ctrl-d to finish   # factory resumes when you exit", file=sys.stderr)

    try:
        found = await _wait_for_sentinel(sentinel_file, timeout)
        if not found:
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{session}:{window}"],
                capture_output=True,
            )
            logger.error("tmux agent timed out after %ss: role=%s", timeout, role)
            _cleanup(tmpdir)
            return f"Agent timed out after {timeout}s", 1, None

        subprocess.run(
            ["tmux", "send-keys", "-t", f"{session}:{window}", "/exit", "Enter"],
            capture_output=True,
        )
        await _wait_for_window_exit(session, window)
        if _window_exists(session, window):
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{session}:{window}"],
                capture_output=True,
            )

        stdout = ""
        return_code = await _wait_for_exitcode(exitcode_file)
        try:
            if logfile.exists():
                stdout = _strip_ansi(logfile.read_text(errors="replace"))
        except OSError as e:
            logger.warning("Failed to read tmux agent output: %s", e)
        finally:
            _cleanup(tmpdir)

        return stdout, return_code, None
    except asyncio.CancelledError:
        if _window_exists(session, window):
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{session}:{window}"],
                capture_output=True,
            )
        _cleanup(tmpdir)
        raise


def _cleanup(tmpdir: Path) -> None:
    shutil.rmtree(tmpdir, ignore_errors=True)
