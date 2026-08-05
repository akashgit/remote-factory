"""Materializing the tree a contained run works on.

A run never writes the host's working tree. It works on a copy, bind-mounted at *its own* absolute
path — identical inside and out — which is what lets the local division's builds resolve a
Containerfile on the host and read what the agent actually wrote. Those builds are executed by an
engine outside the container (spec §5), which resolves the context path in its own filesystem
namespace; mounting the copy at the *original* path instead would give the agent one tree and the
build another, and the divergence surfaces as "file not found" for a file the agent can plainly see.

The copy always starts from the files on this machine, uncommitted changes included (spec §2.1a).
Git projects get a worktree from HEAD — cheap, sharing the object store, and the run's work is
already on a branch when it comes back — and the working tree is then synced over the top, because
the whole point of a contained run is to exercise code that is not committed yet. Everything else
is rsynced.

Copies live under `~/.factory-contained/`, deliberately not under `~/.factory/`, which is itself
bind-mounted read-write (spec §3.3) — nesting them would produce overlapping bind mounts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

CONTAINED_HOME_ENV = "FACTORY_CONTAINED_HOME"
DEFAULT_CONTAINED_HOME = "~/.factory-contained"
BRANCH_PREFIX = "contained"


class WorkspaceError(RuntimeError):
    """Materialization failed in a way the caller should report rather than retry."""


@dataclass(frozen=True)
class Workspace:
    """The copy a run works on, and how to get its result back."""

    source: Path
    path: Path
    kind: str
    branch: str | None = None


def contained_home() -> Path:
    return Path(os.environ.get(CONTAINED_HOME_ENV, DEFAULT_CONTAINED_HOME)).expanduser()


def materialize(source: Path, run_id: str, *, self_contained: bool = False) -> Workspace:
    """Create (or reuse) the run's copy of `source`.

    Idempotent: an existing copy for the same run is refreshed rather than replaced, because a
    reattached run's in-progress work lives there.
    """
    ws = plan_workspace(source, run_id, self_contained=self_contained)
    ws.path.parent.mkdir(parents=True, exist_ok=True)
    if ws.kind == "worktree":
        return _materialize_worktree(ws.source, ws.path, run_id)
    return _materialize_copy(ws.source, ws.path)


def plan_workspace(source: Path, run_id: str, *, self_contained: bool = False) -> Workspace:
    """The `Workspace` `materialize` would produce, without creating or touching anything.

    Dry-run needs the destination path, kind, and branch name in advance — the same values
    `materialize` computes — without `materialize`'s side effects: no directory is created, no
    worktree is added, nothing is rsynced. The one filesystem interaction that survives is the git
    repo check, a read-only `rev-parse` that decides `worktree` vs. `copy`; it changes nothing.

    **`self_contained` is what the cluster target needs, and it is not an optimization.** A git
    worktree's `.git` is a *file* pointing at the original repository's object store. Locally that
    store is bind-mounted and everything works; in a pod there is no host to point at, so `git
    status` fails, state detection reports `no_repo`, and the CEO silently drops to build mode —
    the exact failure the `git_usable` probe exists to catch, and it catches it. A plain copy
    carries a real `.git` directory and stands on its own. The worktree's advantages — cheap,
    shared object store, work already on a branch — are all host-side, and the cluster brings its
    work back as a tarball rather than as a branch anyway.
    """
    source = source.expanduser().resolve()
    destination = contained_home() / run_id / source.name
    if is_git_repo(source) and not self_contained:
        return Workspace(
            source=source, path=destination, kind="worktree", branch=f"{BRANCH_PREFIX}/{run_id}"
        )
    return Workspace(source=source, path=destination, kind="copy")


def is_git_repo(source: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_common_dir(source: Path) -> Path | None:
    """The repository's object store — what a worktree's `.git` *file* points at.

    A worktree's `.git` is a file, not a directory, so the original repository's git directory has
    to be mounted too or every git command inside the container fails on a path that exists on the
    host and not in the container (spec §3.2). `--git-common-dir` rather than `--git-dir` because
    the source may itself be a worktree, in which case only the common dir holds the objects.
    """
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def _materialize_worktree(source: Path, destination: Path, run_id: str) -> Workspace:
    branch = f"{BRANCH_PREFIX}/{run_id}"
    is_new = not destination.exists()
    if is_new:
        # A copy deleted by hand — `rm -rf ~/.factory-contained/<run>` — leaves git still believing
        # a worktree is checked out there, and the branch stays claimed by it. Every later run of
        # the same name then fails on "cannot force update the branch ... used by worktree at",
        # naming a directory that no longer exists. Pruning first is cheap and only ever removes
        # registrations whose directory is already gone.
        _git(source, ["worktree", "prune"])
        _git(source, ["worktree", "add", "--force", "-B", branch, str(destination), "HEAD"])
        log.info("contained_worktree_created", path=str(destination), branch=branch)
    # A worktree carries committed state only. The point of a contained run is to exercise what is
    # *not* committed, so the working tree — modifications, untracked files, and the gitignored
    # .factory/ directory the whole experiment history lives in — is synced over the top.
    # `--delete-after` only runs on that first sync, so it can mirror deletions made in the working
    # tree since HEAD; a later reattach must not delete-after, or it would wipe the in-progress work
    # the run has since written into the copy but never had a source-side counterpart.
    #
    # The exclude has no trailing slash on purpose: in `source` .git is a real directory, but in a
    # worktree checkout it is a plain pointer *file* ("gitdir: ..."). A trailing-slash pattern only
    # matches directories, so it would leave the destination's .git file unprotected and
    # --delete-after would remove it on the first sync — silently breaking `git worktree remove`.
    _rsync(source, destination, exclude=(".git",), delete=is_new)
    return Workspace(source=source, path=destination, kind="worktree", branch=branch)


def _materialize_copy(source: Path, destination: Path) -> Workspace:
    is_new = not destination.exists()
    destination.mkdir(parents=True, exist_ok=True)
    _rsync(source, destination, exclude=(), delete=is_new)
    return Workspace(source=source, path=destination, kind="copy")


def _rsync(source: Path, destination: Path, *, exclude: tuple[str, ...], delete: bool) -> None:
    if shutil.which("rsync") is None:
        raise WorkspaceError(
            "rsync is required to materialize a contained workspace and was not found on PATH. "
            "Install it (`brew install rsync`) and retry."
        )
    argv = ["rsync", "-a"]
    if delete:
        argv.append("--delete-after")
    for pattern in exclude:
        argv += ["--exclude", pattern]
    argv += [f"{source}/", f"{destination}/"]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise WorkspaceError(f"copying {source} into {destination} failed: {result.stderr.strip()}")


def _git(cwd: Path, argv: list[str]) -> None:
    result = subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True)
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(argv)} failed: {result.stderr.strip()}")


def merge_hint(ws: Workspace) -> str:
    """How to bring the run's work back. Never performed automatically."""
    if ws.kind == "worktree" and ws.branch:
        return (
            f"Work is on branch {ws.branch} in {ws.path}.\n"
            f"  Review:  git -C {ws.path} status && git -C {ws.path} diff\n"
            f"  Merge:   git -C {ws.source} merge {ws.branch}"
        )
    return (
        f"Work is in {ws.path}.\n"
        f"  Review:  diff -ru {ws.source} {ws.path}\n"
        f"  Merge:   rsync -a --exclude .git {ws.path}/ {ws.source}/"
    )


def release(ws: Workspace, *, delete_branch: bool = False) -> None:
    """Remove the copy, and optionally the branch that went with it.

    The branch normally survives, because it is where the run's work is. `delete_branch` is for the
    case where there is provably no work to lose — a launch that failed before the factory ever
    started.
    """
    if ws.kind == "worktree":
        _git(ws.source, ["worktree", "remove", "--force", str(ws.path)])
        if delete_branch and ws.branch:
            # Best effort: a branch that was never checked out anywhere is unremarkable to lose, and
            # failing to delete it must not turn a cleanup into a second error.
            subprocess.run(
                ["git", "-C", str(ws.source), "branch", "-D", ws.branch],
                capture_output=True, text=True,
            )
    else:
        shutil.rmtree(ws.path, ignore_errors=True)
    log.debug("contained_workspace_released", path=str(ws.path), kind=ws.kind)


def cleanup_hint(ws: Workspace) -> str:
    """The exact commands that remove what a run left in the *source* repository.

    A worktree is registered in the source repo's git directory and its branch lives in the source
    repo's refs, so removing the container is not the whole story. Deleting the copy's directory by
    hand leaves a stale registration behind, which then blocks the next run of the same name.
    """
    if ws.kind != "worktree" or not ws.branch:
        return f"Remove the copy with:  rm -rf {ws.path}"
    return (
        "This run left a git worktree and a branch in your repository. Remove them with:\n"
        f"  git -C {ws.source} worktree remove {ws.path}\n"
        f"  git -C {ws.source} branch -D {ws.branch}"
    )
