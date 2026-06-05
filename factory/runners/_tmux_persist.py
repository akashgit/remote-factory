"""Tmux persist — launch agents interactively in tmux with output capture."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_PREFIX = "factory-persist-"


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


_DEFAULT_TMUX_TIMEOUT = 86400.0  # 24 hours — interactive sessions are user-driven


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

    Output is captured via the `script` command. The factory blocks on
    `tmux wait-for` until the session exits, then reads the captured output.

    Returns (stdout, return_code, None). Usage is always None for tmux mode.
    """
    run_id = uuid.uuid4().hex[:8]
    signal = f"factory-done-{run_id}"
    path_hash = hashlib.sha1(str(project_path).encode()).hexdigest()[:6]
    session = f"{_SESSION_PREFIX}{project_path.name}-{path_hash}"
    window = f"{role}-{run_id}"

    tmpdir = Path(tempfile.mkdtemp(prefix="factory-tmux-"))
    logfile = tmpdir / "output.log"
    exitcode_file = tmpdir / "exitcode"
    wrapper_script = tmpdir / "wrapper.sh"

    prompt_file = tmpdir / "prompt.md"
    prompt_file.write_text(prompt)

    cmd = ["claude", "--append-system-prompt-file", str(prompt_file)]
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

    wrapper_script.write_text(
        "#!/bin/bash\n"
        f"{script_line}"
        f"echo $? > {shlex.quote(str(exitcode_file))}\n"
        f"tmux wait-for -S {shlex.quote(signal)}\n"
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
        wait_proc = await asyncio.create_subprocess_exec(
            "tmux", "wait-for", signal,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(wait_proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        wait_proc.kill()
        await wait_proc.wait()
        subprocess.run(
            ["tmux", "kill-window", "-t", f"{session}:{window}"],
            capture_output=True,
        )
        logger.error("tmux agent timed out after %ss: role=%s", timeout, role)
        _cleanup(tmpdir)
        return f"Agent timed out after {timeout}s", 1, None

    stdout = ""
    return_code = 1
    try:
        if logfile.exists():
            stdout = _strip_ansi(logfile.read_text(errors="replace"))
        if exitcode_file.exists():
            return_code = int(exitcode_file.read_text().strip())
    except (ValueError, OSError) as e:
        logger.warning("Failed to read tmux agent output: %s", e)
    finally:
        _cleanup(tmpdir)

    return stdout, return_code, None


def _cleanup(tmpdir: Path) -> None:
    try:
        for f in tmpdir.iterdir():
            f.unlink()
        tmpdir.rmdir()
    except OSError:
        pass
