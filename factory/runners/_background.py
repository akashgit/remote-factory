"""Background agent execution via claude --bg."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_BG_POLL_INTERVAL = 5.0
_BG_TERMINAL_STATES = {"done", "completed", "failed", "stopped"}
_CLAUDE_JOBS_DIR = Path("~/.claude/jobs").expanduser()

_DEFAULT_BG_TIMEOUT = 86400.0


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _parse_bg_session_id(output: str) -> str | None:
    """Parse session ID from ``claude --bg`` output.

    Expected format: 'backgrounded · <hex_id> [· <name>]'
    """
    for line in output.splitlines():
        if line.startswith("backgrounded"):
            parts = line.split("·")
            if len(parts) >= 2:
                return parts[1].strip()
    return None


def _read_session_state(session_id: str) -> dict | None:
    state_file = _CLAUDE_JOBS_DIR / session_id / "state.json"
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


async def run_in_background(
    prompt: str,
    task: str,
    cwd: Path,
    role: str,
    *,
    timeout: float = _DEFAULT_BG_TIMEOUT,
    model: str | None = None,
    dangerously_skip_permissions: bool = True,
) -> tuple[str, int, None]:
    """Launch claude as a background session via --bg (agent view).

    Returns (stdout, return_code, None). Usage is always None for bg mode.
    """
    session_name = f"factory-{role}"

    prompt_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="factory-bg-prompt-", delete=False,
    )
    prompt_file.write(prompt)
    prompt_file.close()
    prompt_path = prompt_file.name

    cmd = [
        "claude", "--bg", "--name", session_name,
        "--append-system-prompt-file", prompt_path, "-p", task,
    ]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if model:
        cmd.extend(["--model", model])

    logger.info("Launching background agent: role=%s, cwd=%s", role, cwd)

    env = dict(os.environ)
    env["FACTORY_BG"] = "1"

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except FileNotFoundError:
        logger.error("'claude' CLI not found on PATH")
        _unlink_quiet(prompt_path)
        return "Error: 'claude' CLI not found on PATH", 1, None
    except subprocess.TimeoutExpired:
        logger.error("claude --bg timed out during launch")
        _unlink_quiet(prompt_path)
        return "Error: claude --bg timed out during launch", 1, None

    output = result.stdout + result.stderr
    session_id = _parse_bg_session_id(output)

    if result.returncode != 0 or not session_id:
        logger.warning("Failed to launch background agent: %s", output[:200])
        _unlink_quiet(prompt_path)
        return f"Failed to launch background agent for {role}: {output[:200]}", 1, None

    print(f"Agent '{role}' launched in background: {session_id}", file=sys.stderr)
    print(f"  claude attach {session_id}    # attach to interact", file=sys.stderr)

    try:
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(_BG_POLL_INTERVAL)
            elapsed += _BG_POLL_INTERVAL

            state = _read_session_state(session_id)
            if state and state.get("state") in _BG_TERMINAL_STATES:
                session_output = ""
                if isinstance(state.get("output"), dict):
                    session_output = state["output"].get("result", "")
                elif isinstance(state.get("output"), str):
                    session_output = state["output"]

                is_success = state["state"] in ("done", "completed")
                return session_output, 0 if is_success else 1, None

        logger.error("Background agent timed out after %ss: role=%s", timeout, role)
        stop_result = subprocess.run(["claude", "stop", session_id], capture_output=True)
        if stop_result.returncode != 0:
            logger.warning(
                "Failed to stop background session %s: %s",
                session_id,
                stop_result.stderr[:200],
            )
        return f"Agent timed out after {timeout}s", 1, None
    finally:
        _unlink_quiet(prompt_path)
