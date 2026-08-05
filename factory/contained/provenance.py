"""Proving the runtime is about to read the files we think it is.

A workspace can be missing, empty, stale, or read-only, and all four look identical until something
is asserted. Each of these failures is silent: the run starts, the agent works on the wrong files,
and the result looks plausible. So they are checked between provisioning and the first agent call,
where a failure costs nothing.

The probes are composed here and executed by the caller, so the same list can be wrapped in
`podman exec` locally or `oc exec` in a pod.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

_HASH_CHUNK = 1 << 20
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


@dataclass(frozen=True)
class Probe:
    """One assertion, as a command to run inside the runtime plus what a failure means."""

    name: str
    argv: list[str] = field(default_factory=list)
    hint: str = ""


def content_probe(root: Path) -> tuple[str, str] | None:
    """Pick a file whose content proves the transfer, and hash it.

    The largest regular file outside `.git/` — deterministic, and large files are the ones a
    truncated or partial transfer mangles. Returns None when there is nothing to hash, in which
    case the check is skipped rather than faked.
    """
    best: tuple[int, Path] | None = None
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        size = path.stat().st_size
        if best is None or size > best[0]:
            best = (size, path)
    if best is None:
        return None
    digest = hashlib.sha256()
    with best[1].open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return str(best[1].relative_to(root)), digest.hexdigest()


def provenance_probes(
    runtime_path: str,
    *,
    expect_factory_state: bool,
    expect_git: bool,
    content: tuple[str, str] | None,
) -> list[Probe]:
    """The assertions to run after the workspace is in place and before the factory starts.

    Each hint states the cause first and then what to do about it. The reason the check exists is
    interesting to whoever maintains this and useless to whoever hit it: the reader wants to know
    what to change.
    """
    probes = [
        Probe(
            name="project_present",
            argv=["sh", "-lc", f'[ -d "{runtime_path}" ] && [ -n "$(ls -A "{runtime_path}")" ]'],
            hint=(
                f"The project directory is empty inside the runtime ({runtime_path}).\n"
                "  On macOS this usually means the path is outside your home directory, which the "
                "podman machine does not share by default.\n"
                "  Try:  move the project under your home directory, or add its path with "
                "`podman machine set --volume` and restart the machine."
            ),
        ),
    ]
    if expect_git:
        probes.append(
            Probe(
                name="git_usable",
                argv=["sh", "-lc", f'git -C "{runtime_path}" status --porcelain >/dev/null 2>&1'],
                hint=(
                    "The workspace is not a usable git repository inside the runtime.\n"
                    "  Most likely the repository this project belongs to was not mounted — a git "
                    "worktree's .git is a file pointing at a directory elsewhere.\n"
                    "  Try:  factory contained --mount <path-to-that-repository> -- <your command>"
                ),
            )
        )
    if expect_factory_state:
        probes.append(
            Probe(
                name="factory_state",
                argv=["test", "-f", f"{runtime_path}/.factory/config.json"],
                hint=(
                    ".factory/config.json did not reach the runtime, though this project has one.\n"
                    "  Without it the run starts as though the project were brand new, and its "
                    "history and scores are not available to it.\n"
                    "  Try:  check that .factory/ exists and is readable in the project directory."
                ),
            )
        )
    probes.append(
        Probe(
            name="writable",
            # Written and removed rather than `test -w`: the mode bits can say writable while the
            # mount is read-only in practice, which is the failure this exists to catch.
            argv=[
                "sh", "-lc",
                f'touch "{runtime_path}/.factory-write-probe" && '
                f'rm -f "{runtime_path}/.factory-write-probe"',
            ],
            hint=(
                "The workspace is read-only inside the runtime, so the agent's edits would be "
                "silently discarded.\n"
                "  The container runs as a user that does not own these files.\n"
                "  Try:  `factory contained verify` to check the runtime image, and make sure the "
                "project is owned by you."
            ),
        )
    )
    if content is not None:
        relative, digest = content
        probes.append(
            Probe(
                name="content_hash",
                argv=[
                    "sh", "-lc",
                    f'sha256sum "{runtime_path}/{relative}" 2>/dev/null | grep -q "^{digest} "',
                ],
                hint=(
                    f"{relative} inside the runtime does not match the copy on this machine, so the "
                    "run would work on the wrong files.\n"
                    "  The path is there but its contents differ — a stale or partial copy.\n"
                    f"  Try:  factory contained rm <name>, then run again to rebuild the workspace."
                ),
            )
        )
    return probes
