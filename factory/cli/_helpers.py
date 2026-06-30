"""CLI _helpers commands."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import structlog
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

log = structlog.get_logger()

_WIZARD_INPUT_PATH = Path("~/.factory/wizard_input.md")


CEO_MODES = ["auto", "auto-fresh", "build", "discover", "improve", "meta", "design", "interactive", "research", "review", "qa", "create"]


RUN_MODES = ["auto", "auto-fresh", "build", "discover", "improve", "meta", "research"]


def _run(coro):  # noqa: ANN001, ANN202
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _detect_pr_number(project_path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number", "-q", ".number"],
            capture_output=True,
            timeout=10,
            cwd=project_path,
        )
        if result.returncode == 0:
            return int(result.stdout.decode().strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        pass
    return None


def _read_target_branch(project_path: Path) -> str:
    """Read target branch from .factory/config.json, falling back to git detection."""
    config_path = project_path / ".factory" / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            tb = config.get("target_branch")
            if tb:
                return tb
        except (json.JSONDecodeError, OSError):
            pass
    from factory.worktree import detect_default_branch

    return detect_default_branch(project_path)


# ── banner ────────────────────────────────────────────────────


_DASHBOARD_PORT = 8420


def _dashboard_is_running(port: int = _DASHBOARD_PORT) -> bool:
    """Check if the dashboard is already listening on the given port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_dashboard(project_path: Path, port: int = _DASHBOARD_PORT) -> None:
    """Start the dashboard in the background if it's not already running.

    Prints the dashboard URL to stderr either way.
    """
    url = f"http://localhost:{port}"

    if _dashboard_is_running(port):
        print(f"  Dashboard: {url} (running)", file=sys.stderr)
        return

    # Determine projects directory (parent of the project)
    projects_dir = project_path.parent

    # Start dashboard as a detached background process
    cmd = [
        sys.executable, "-m", "factory", "dashboard",
        "--projects-dir", str(projects_dir),
        "--port", str(port),
        "--host", "0.0.0.0",
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach from parent process
    )
    print(f"  Dashboard: {url} (started)", file=sys.stderr)


def _print_banner(mode: str = "improve") -> None:
    """Print the Factory startup banner to stderr."""
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        if mode == "welcome":
            print("The Factory — Self-Evolving Meta-Harness", file=sys.stderr)
        else:
            print(f"Factory v2 — mode: {mode}", file=sys.stderr)
        return

    c = "\033[1;36m"  # bold cyan
    d = "\033[2m"      # dim
    r = "\033[0m"      # reset

    mode_line = "" if mode == "welcome" else f"{d}  Mode: {mode}{r}\n"
    banner = (
        f"\n{c}  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻{r}\n"
        f"{c}  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛{r}\n"
        f"{c}  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹ {r}\n"
        f"{d}  Self-Evolving Meta-Harness{r}\n"
        f"{mode_line}"
    )
    print(banner, file=sys.stderr)


# ── welcome wizard ─────────────────────────────────────────────


_BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _show_spinner(stop_event: threading.Event) -> None:
    """Braille spinner on stderr. Respects NO_COLOR."""
    use_color = not os.environ.get("NO_COLOR") and sys.stderr.isatty()
    idx = 0
    while not stop_event.is_set():
        frame = _BRAILLE_FRAMES[idx % len(_BRAILLE_FRAMES)]
        if use_color:
            sys.stderr.write(f"\r\033[2m  Thinking... {frame}\033[0m")
        else:
            sys.stderr.write(f"\r  Thinking... {frame}")
        sys.stderr.flush()
        idx += 1
        stop_event.wait(0.1)
    if use_color:
        sys.stderr.write("\r\033[2K")
    else:
        sys.stderr.write("\r" + " " * 30 + "\r")
    sys.stderr.flush()


def _safe_is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except (OSError, ValueError):
        return False


def _safe_is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except (OSError, ValueError):
        return False


def _emit_cli_event(project_path: Path, event_type: str, data: dict) -> None:
    """Emit a factory event, swallowing errors."""
    try:
        from factory.events import emit_event

        emit_event(project_path, event_type, data=data)
    except Exception:
        pass


# ── parser construction ────────────────────────────────────────


def _load_env_local() -> None:
    """Auto-load .env.local if present, exporting vars into os.environ."""
    for candidate in [Path(".env.local"), Path.home() / "remote-factory" / ".env.local"]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
            break

