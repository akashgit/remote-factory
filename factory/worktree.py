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
    run_id = secrets.token_hex(4)
    branch = f"factory/run-{run_id}"
    wt_dir = project_path / ".factory" / "worktrees" / f"run-{run_id}"

    log.info("worktree_create", branch=branch, path=str(wt_dir))

    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "-b", branch, base_branch],
        cwd=project_path,
        check=True,
        capture_output=True,
    )

    factory_real = (project_path / ".factory").resolve()
    wt_factory = wt_dir / ".factory"
    if wt_factory.exists() or wt_factory.is_symlink():
        if wt_factory.is_dir() and not wt_factory.is_symlink():
            shutil.rmtree(wt_factory)
        else:
            wt_factory.unlink()
    wt_factory.symlink_to(factory_real)

    log.info("worktree_created", branch=branch, path=str(wt_dir))
    return wt_dir, branch


def remove_worktree(project_path: Path, worktree_path: Path, branch: str) -> None:
    """Remove a worktree and its branch. Safe to call on already-removed paths."""
    log.info("worktree_remove", branch=branch, path=str(worktree_path))

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
    factory_dir = project_path / ".factory"
    if not factory_dir.is_dir():
        return []

    result = subprocess.run(
        ["git", "worktree", "prune", "--verbose"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    pruned = [line for line in result.stderr.splitlines() if "Removing" in line]

    wt_parent = factory_dir / "worktrees"
    if wt_parent.exists():
        active = _list_active_worktrees(project_path)
        for d in wt_parent.iterdir():
            if d.is_dir() and str(d.resolve()) not in active:
                shutil.rmtree(d)
                pruned.append(f"Removed orphaned directory: {d.name}")
                log.info("worktree_pruned_orphan", name=d.name)

    if pruned:
        log.info("worktree_prune_complete", pruned_count=len(pruned))

    return pruned


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
