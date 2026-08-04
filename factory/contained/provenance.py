"""Proving the runtime is about to read the files we think it is (spec §2.1a).

Every check here corresponds to a failure that has already been paid for once, and every one of
them fails *quietly* without it: a run that starts on the wrong files burns a full cycle and
produces a plausible-looking result.

The probes are composed here and executed by the caller, so the same list can be wrapped in
`podman exec` locally or `oc exec` in a pod.

The `.gitignore` failure mode these were originally written against was specific to a transfer that
filtered its input, which is why `.factory/` — gitignored by convention — used to vanish. A bind
mount filters nothing, so locally that class of fault is gone. The checks stay because they are
cheap, and because the k8s path still packs a file list, where the fault is live.
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
    """The assertions to run after the workspace is in place and before the factory starts."""
    probes = [
        Probe(
            name="project_present",
            argv=["sh", "-lc", f'[ -d "{runtime_path}" ] && [ -n "$(ls -A "{runtime_path}")" ]'],
            hint=(
                f"{runtime_path} is missing or empty inside the runtime. A mount whose destination "
                "nests the tree one level deeper leaves the factory starting in an empty directory, "
                "and on macOS a host path outside $HOME is not shared into the podman machine at "
                "all."
            ),
        ),
    ]
    if expect_git:
        probes.append(
            Probe(
                name="git_usable",
                argv=["sh", "-lc", f'git -C "{runtime_path}" status --porcelain >/dev/null 2>&1'],
                hint=(
                    "git is not usable in the workspace. State detection then reports no_repo, the "
                    "CEO silently drops to build mode, and the eventual error names a flag several "
                    "steps away from the cause. For a git worktree this usually means the source "
                    "repository's git directory is not mounted — a worktree's .git is a *file* "
                    "pointing at it."
                ),
            )
        )
    if expect_factory_state:
        probes.append(
            Probe(
                name="factory_state",
                argv=["test", "-f", f"{runtime_path}/.factory/config.json"],
                hint=(
                    ".factory/config.json did not arrive, though the host has one. The experiment "
                    "history, eval profile and config are gone and the factory will boot as a "
                    "fresh project and re-run discovery."
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
                "The workspace is not writable by the runtime identity. A bind mount carries the "
                "host's ownership through unchanged, so a container whose UID does not own the "
                "mounted tree gets a silently read-only workspace — surfacing several steps later "
                "as an agent unable to explain why its edits vanished."
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
                    f"{relative} inside the runtime does not match the host's copy. The path exists "
                    "but its content is stale or partial — the one check that catches a mount "
                    "pointing at the wrong directory."
                ),
            )
        )
    return probes
