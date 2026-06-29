"""Git worktree lifecycle management for experiment isolation."""

import secrets
import shutil
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()


def create_worktree(project_path: Path, base_branch: str = "main") -> tuple[Path, str]:
    """Create an isolated worktree for a factory run.

    Returns (worktree_path, branch_name).
    """
    project_path = project_path.resolve()

    # Resolve symbolic refs (HEAD, branch names) to commit SHAs so the
    # worktree always branches from a deterministic point — critical when
    # HEAD was just amended (e.g. FeatureBench mask-patch scenario).
    result = subprocess.run(
        ["git", "rev-parse", base_branch],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=True,
    )
    base_commit = result.stdout.strip()

    run_id = secrets.token_hex(4)
    branch = f"factory/run-{run_id}"
    factory_dir = project_path / ".factory"
    wt_parent = project_path / ".factory-worktrees"
    wt_dir = wt_parent / f"run-{run_id}"

    log.info("worktree_create", branch=branch, base=base_commit[:12], path=str(wt_dir))

    wt_parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "-b", branch, base_commit],
        cwd=project_path,
        check=True,
        capture_output=True,
    )

    # Symlink worktree/.factory → the real .factory dir so the CEO can
    # access experiment data from within the worktree.
    wt_factory = wt_dir / ".factory"
    if wt_factory.exists() or wt_factory.is_symlink():
        if wt_factory.is_dir() and not wt_factory.is_symlink():
            shutil.rmtree(wt_factory)
        else:
            wt_factory.unlink()
    wt_factory.symlink_to(factory_dir)

    log.info("worktree_created", branch=branch, path=str(wt_dir))

    try:
        from factory.events import emit_event
        emit_event(project_path, "worktree.created", data={
            "run_id": run_id,
            "worktree_path": str(wt_dir),
            "branch": branch,
            "base_branch": base_branch,
        })
    except Exception:
        pass

    return wt_dir, branch


def remove_worktree(project_path: Path, worktree_path: Path, branch: str) -> None:
    """Remove a worktree and its branch. Safe to call on already-removed paths."""
    log.info("worktree_remove", branch=branch, path=str(worktree_path))

    run_id = branch.removeprefix("factory/run-")
    try:
        from factory.events import emit_event
        emit_event(project_path, "worktree.removed", data={
            "run_id": run_id,
            "branch": branch,
        })
    except Exception:
        pass

    if worktree_path.exists():
        shutil.rmtree(worktree_path)

    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=project_path,
        capture_output=True,
    )

    subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=project_path,
        capture_output=True,
    )


def prune_stale(project_path: Path) -> list[str]:
    """Clean up stale worktrees from crashed runs. Returns list of pruned entries."""
    project_path = project_path.resolve()
    if not project_path.exists():
        return []

    result = subprocess.run(
        ["git", "worktree", "prune", "--verbose"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    pruned = [line for line in result.stderr.splitlines() if "Removing" in line]

    # Check both current (.factory-worktrees/) and legacy (.factory/worktrees/) locations
    wt_parents = [
        project_path / ".factory-worktrees",
        project_path / ".factory" / "worktrees",
    ]
    active: set[str] | None = None
    for wt_parent in wt_parents:
        if not wt_parent.is_dir():
            continue
        if active is None:
            active = _list_active_worktrees(project_path)
        for d in wt_parent.iterdir():
            if d.is_dir() and str(d.resolve()) not in active:
                run_id = d.name.removeprefix("run-")
                shutil.rmtree(d)
                pruned.append(f"Removed orphaned directory: {d.name}")
                log.info("worktree_pruned_orphan", name=d.name)
                branch = f"factory/run-{run_id}"
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=project_path,
                    capture_output=True,
                )

    if pruned:
        log.info("worktree_prune_complete", pruned_count=len(pruned))

    return pruned


def detect_default_branch(project_path: Path) -> str:
    """Detect the default branch for a git repository.

    Cascade: remote HEAD → probe main/master → current HEAD → fallback 'main'.
    """
    project_path = project_path.resolve()

    # Try remote default branch
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        branch = ref.removeprefix("refs/remotes/origin/")
        if branch and branch != ref:
            log.debug("detect_default_branch", source="remote_head", branch=branch)
            return branch

    # Probe main then master
    for candidate in ("main", "master"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            log.debug("detect_default_branch", source="probe", branch=candidate)
            return candidate

    # Current branch
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        branch = result.stdout.strip()
        if branch != "HEAD":
            log.debug("detect_default_branch", source="current_head", branch=branch)
            return branch

    log.debug("detect_default_branch", source="fallback", branch="main")
    return "main"


def _list_active_worktrees(project_path: Path) -> set[str]:
    """Return set of absolute paths for all active worktrees."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    return {
        line.split(" ", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }
