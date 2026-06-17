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


def _capture_pane(session: str, window: str) -> str:
    """Capture tmux pane content via capture-pane -p."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", f"{session}:{window}", "-p", "-S", "-"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout
    return ""


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
    runner_cmd: list[str] | None = None,
    runner_env: dict[str, str] | None = None,
    needs_real_tty: bool = False,
) -> tuple[str, int, None]:
    """Launch a runner interactively in a tmux window and wait for completion.

    Output is captured via the `script` command. Completion is signaled via
    the wrapper script's trap EXIT handler. For Claude, Stop/StopFailure
    hooks are added as an optional optimization for faster detection.

    When ``runner_cmd`` is provided, it is used as the command to run.
    Otherwise falls back to building a Claude command (backwards compat).

    When ``needs_real_tty`` is True, the runner is launched via ``tmux
    send-keys`` instead of as the tmux window command.  This gives the
    process a real ``/dev/tty``, which TUI frameworks like Bubble Tea
    (used by OpenCode) require.  Output is captured via ``tmux
    capture-pane`` instead of the ``script`` command.

    Returns (stdout, return_code, None). Usage is always None for tmux mode.
    """
    run_id = uuid.uuid4().hex[:8]
    path_hash = hashlib.sha1(str(project_path).encode()).hexdigest()[:6]
    session = f"{_SESSION_PREFIX}{project_path.name}-{path_hash}"
    window = f"{role}-{run_id}"

    tmpdir = Path(tempfile.mkdtemp(prefix="factory-tmux-"))
    exitcode_file = tmpdir / "exitcode"
    sentinel_file = tmpdir / "sentinel"
    sentinel_q = shlex.quote(str(sentinel_file))
    exitcode_q = shlex.quote(str(exitcode_file))

    is_claude = runner_cmd is None or (runner_cmd and runner_cmd[0] == "claude")

    if runner_cmd is not None:
        cmd = list(runner_cmd)
    else:
        prompt_file = tmpdir / "prompt.md"
        prompt_file.write_text(prompt)
        settings_file = _generate_settings(sentinel_file, tmpdir, project_path)
        cmd = ["claude", "--settings", str(settings_file), "--append-system-prompt-file", str(prompt_file)]
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model:
            cmd.extend(["--model", model])
        cmd.append(task)

    full_cmd = shlex.join(cmd)

    if needs_real_tty:
        return await _run_in_tmux_sendkeys(
            full_cmd, cwd, role, project_path, session, window, tmpdir,
            exitcode_file, sentinel_file, exitcode_q, sentinel_q,
            runner_env=runner_env, timeout=timeout,
        )

    logfile = tmpdir / "output.log"
    wrapper_script = tmpdir / "wrapper.sh"

    logfile_q = shlex.quote(str(logfile))
    if platform.system() == "Darwin":
        script_line = f"script -q {logfile_q} {full_cmd}\n"
    else:
        script_line = f"script -q -c {shlex.quote(full_cmd)} {logfile_q}\n"

    env_lines = ""
    if runner_env:
        for k, v in runner_env.items():
            env_lines += f"export {shlex.quote(k)}={shlex.quote(v)}\n"

    wrapper_script.write_text(
        "#!/bin/bash\n"
        f"{env_lines}"
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

    # Auto-accept trust prompt (Claude and Codex show 'Do you trust?' for untrusted dirs)
    await asyncio.sleep(3)
    subprocess.run(["tmux", "send-keys", "-t", f"{session}:{window}", "Enter"], capture_output=True)

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

        if is_claude:
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


async def _run_in_tmux_sendkeys(
    full_cmd: str,
    cwd: Path,
    role: str,
    project_path: Path,
    session: str,
    window: str,
    tmpdir: Path,
    exitcode_file: Path,
    sentinel_file: Path,
    exitcode_q: str,
    sentinel_q: str,
    *,
    runner_env: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TMUX_TIMEOUT,
) -> tuple[str, int, None]:
    """Launch a runner via tmux send-keys for proper PTY allocation.

    TUI frameworks like Bubble Tea (OpenCode) require /dev/tty access.
    When tmux runs a command directly (``tmux new-session -d cmd``), the
    process doesn't get a proper PTY.  This helper creates the session
    with a bare shell, then sends the command via ``send-keys``.
    """
    has_session = _session_exists(session)
    if has_session:
        result = subprocess.run(
            ["tmux", "new-window", "-t", session, "-n", window, "-c", str(cwd)],
            capture_output=True,
        )
    else:
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-n", window,
             "-x", "200", "-y", "50", "-c", str(cwd)],
            capture_output=True,
        )

    if result.returncode != 0:
        logger.warning("Failed to create tmux window for %s: %s", role, result.stderr.decode()[:200])
        _cleanup(tmpdir)
        return f"Failed to create tmux window for {role}", 1, None

    logger.info("tmux_launched_sendkeys session=%s window=%s role=%s", session, window, role)
    print(f"Agent '{role}' launched in tmux session: {session}", file=sys.stderr)
    print(f"  tmux attach -t {session}    # attach and interact", file=sys.stderr)

    target = f"{session}:{window}"

    if runner_env:
        env_exports = " && ".join(
            f"export {shlex.quote(k)}={shlex.quote(v)}" for k, v in runner_env.items()
        )
        subprocess.run(["tmux", "send-keys", "-t", target, env_exports, "Enter"], capture_output=True)
        await asyncio.sleep(0.3)

    cmd_with_sentinel = f"{full_cmd}; echo $? > {exitcode_q}; touch {sentinel_q}"
    subprocess.run(["tmux", "send-keys", "-t", target, cmd_with_sentinel, "Enter"], capture_output=True)

    try:
        found = await _wait_for_sentinel(sentinel_file, timeout)
        if not found:
            subprocess.run(["tmux", "kill-window", "-t", target], capture_output=True)
            logger.error("tmux agent timed out after %ss: role=%s", timeout, role)
            _cleanup(tmpdir)
            return f"Agent timed out after {timeout}s", 1, None

        stdout = _strip_ansi(_capture_pane(session, window))

        await _wait_for_window_exit(session, window)
        if _window_exists(session, window):
            subprocess.run(["tmux", "kill-window", "-t", target], capture_output=True)

        return_code = await _wait_for_exitcode(exitcode_file)
        _cleanup(tmpdir)
        return stdout, return_code, None
    except asyncio.CancelledError:
        if _window_exists(session, window):
            subprocess.run(["tmux", "kill-window", "-t", target], capture_output=True)
        _cleanup(tmpdir)
        raise


def _cleanup(tmpdir: Path) -> None:
    shutil.rmtree(tmpdir, ignore_errors=True)
