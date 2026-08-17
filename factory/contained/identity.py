"""Which UID the container runs as, decided by measurement rather than by rule.

Identity is the trap. A bind mount carries ownership through unchanged, so a container whose UID
does not own the mounted tree gets a silently read-only workspace — a failure that surfaces several
steps later as an agent unable to explain why its edits vanished.

The mechanism differs by how podman is running, and no single rule is right for all three:

- **Rootless podman:** `--userns=keep-id` maps the host UID into the container, so files the host
  user owns are owned by the container user. This is the intended configuration.
- **Rootful podman:** the mapping is different, and the runtime image's default UID matches neither
  the host user nor root.
- **macOS:** the container runs inside the podman machine VM and the host path reaches it through
  the VM's filesystem sharing, so what the container sees is what the VM's sharing layer decided —
  not what `ls -l` on the host says.

Rather than encode a rule that is wrong for one of these, this module **asks**: it starts a
throwaway container with the same mount and reads back the owner the kernel reports inside. The
probe is the contract; `--userns` and `--user` are implementation details that may change per
platform, and the writability probe in `provenance.py` is the second, independent check that the
answer was right.

Group 0 is used rather than the mount's own GID because the runtime image follows the arbitrary-UID
convention (files group-owned by root with group permissions equal to user permissions), which is
also what the cluster's restricted SCC requires. One image, one identity story.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

import structlog

from factory.podman import Mount, build_info_argv, build_stat_argv

log = structlog.get_logger()


class IdentityError(RuntimeError):
    """The container identity could not be determined, so nothing should be provisioned."""


@dataclass(frozen=True)
class Identity:
    """How to run the container so it can write the workspace."""

    user: str | None
    userns: str | None
    detail: str


def podman_is_rootless() -> bool | None:
    """Whether podman's active connection is rootless. None when podman cannot be reached."""
    try:
        result = subprocess.run(build_info_argv(), capture_output=True, text=True)
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        info = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    security = info.get("host", {}).get("security", {})
    rootless = security.get("rootless")
    return bool(rootless) if isinstance(rootless, bool) else None


def mount_owner(image: str, mount: Mount) -> tuple[int, int] | None:
    """The mount's owner as the *container* sees it, or None when the probe could not run."""
    try:
        result = subprocess.run(
            build_stat_argv(image, mount), capture_output=True, text=True, timeout=120
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        log.warning("contained_identity_probe_failed", stderr=result.stderr.strip()[:200])
        return None
    raw = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    uid, _, gid = raw.partition(":")
    try:
        return int(uid), int(gid)
    except ValueError:
        return None


def resolve_identity(image: str, mount: Mount, *, dry_run: bool = False) -> Identity:
    """Decide the container identity for a workspace mount.

    Dry-run projects the answer from the host UID instead of starting a probe container: composing
    a command must not provision anything, and the argv shape is identical either way.
    """
    if dry_run:
        return Identity(
            user=f"{os.getuid()}:0",
            userns=None,
            detail=f"dry-run: identity projected from the host UID ({os.getuid()}), not probed",
        )

    rootless = podman_is_rootless()
    if rootless:
        # keep-id maps the host UID straight through, which is exactly the property needed, and it
        # is unavailable to a rootful connection (podman rejects it outright).
        return Identity(
            user=None,
            userns="keep-id",
            detail=f"rootless podman: --userns=keep-id maps host UID {os.getuid()} into the container",
        )

    owner = mount_owner(image, mount)
    if owner is None:
        raise IdentityError(
            f"could not read {mount.target} from inside a container, so the run cannot start.\n"
            "  Most likely one of:\n"
            "    - the podman machine does not share this path (macOS shares your home directory "
            "by default)\n"
            f"    - the runtime image is missing — run `factory contained verify`\n"
            "    - the podman machine is not running — run `podman machine start`\n"
            f"  To see the failure yourself:\n"
            f"    podman run --rm -v {mount.as_flag()} {image} stat -c '%u:%g' {mount.target}"
        )
    uid, gid = owner
    return Identity(
        user=f"{uid}:0",
        userns=None,
        detail=(
            f"rootful podman: the workspace is owned by {uid}:{gid} inside a container, so the run "
            f"uses --user {uid}:0 (group 0 because the runtime image is built for arbitrary UIDs)"
        ),
    )
