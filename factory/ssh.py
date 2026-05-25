"""SSH agent connectivity checks for factory agent spawning."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()


@dataclass
class SSHCheckResult:
    """Result of an SSH agent connectivity check."""

    has_ssh_remotes: bool
    agent_socket_set: bool
    agent_socket_exists: bool
    needs_warning: bool


def check_ssh_agent(project_path: Path) -> SSHCheckResult:
    """Check SSH agent availability relative to a project's git remotes.

    Non-blocking: emits a structlog warning when the project uses SSH remotes
    but the agent socket is missing or stale. Never aborts.
    """
    has_ssh = _has_ssh_remotes(project_path)
    sock = os.environ.get("SSH_AUTH_SOCK", "")
    sock_set = bool(sock)
    sock_exists = sock_set and Path(sock).exists()

    needs_warning = has_ssh and not sock_exists

    if needs_warning:
        if not sock_set:
            log.warning(
                "ssh_agent_missing",
                project=project_path.name,
                hint="SSH_AUTH_SOCK is not set. Run: eval $(ssh-agent) && ssh-add",
            )
        else:
            log.warning(
                "ssh_agent_stale",
                project=project_path.name,
                socket=sock,
                hint="SSH_AUTH_SOCK points to a missing socket. Re-run: eval $(ssh-agent) && ssh-add",
            )

    return SSHCheckResult(
        has_ssh_remotes=has_ssh,
        agent_socket_set=sock_set,
        agent_socket_exists=sock_exists,
        needs_warning=needs_warning,
    )


def _has_ssh_remotes(project_path: Path) -> bool:
    """Check if the project has any SSH git remotes."""
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            cwd=project_path,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            if "git@" in line or "ssh://" in line:
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False
