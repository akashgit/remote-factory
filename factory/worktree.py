"""Git worktree lifecycle management for experiment isolation."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Final

import structlog


log = structlog.get_logger()

# Telemetry files to preserve when cleaning up worktrees
_TELEMETRY_FILES = ("trace_id.txt",)

# .factory entries to seed into experiment worktrees so agents can read project
# config without sharing mutable eval state (like last_eval.json) across branches.
_EXPERIMENT_SEED_ENTRIES: Final[tuple[str, ...]] = (
    "config.json",
    "eval_profile.json",
    "strategy",
    "agents",
)

# .factory entries symlinked to main — shared, append-only/read-only project state.
_SHARED_SYMLINK_ENTRIES: Final[tuple[str, ...]] = (
    "config.json",
    "eval_profile.json",
    "results.tsv",
    "experiments",
    "archive",
    "events.jsonl",
    ".store.lock",
    "adversarial_state.json",
    "performance_report.json",
)

# .factory entries copied from main — read-only but agents may override per-run.
_COPY_ENTRIES: Final[tuple[str, ...]] = (
    "agents",
)


def create_worktree(
    project_path: Path,
    base_branch: str = "main",
    run_id: str | None = None,
) -> tuple[Path, str]:
    """Create an isolated worktree for a factory run.

    Args:
        project_path: Path to the project root.
        base_branch: Branch to create the worktree from.
        run_id: Optional run identifier. If provided, uses the first 8 chars.
                If None, generates a random 8-char hex ID.

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
    )
    if result.returncode != 0:
        if _is_unborn_repo(project_path):
            _bootstrap_unborn_repo(project_path)
            result = subprocess.run(
                ["git", "rev-parse", base_branch],
                cwd=project_path,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            raise RuntimeError(
                f"Branch '{base_branch}' does not exist in {project_path}. "
                "Set `target_branch` in .factory/config.json or check your git state."
            )
    base_commit = result.stdout.strip()

    if run_id is not None:
        run_id = run_id[:8]
    else:
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

    # Create independent .factory/ with selective sharing — shared append-only
    # state is symlinked, per-cycle mutable state gets fresh directories.
    wt_factory = wt_dir / ".factory"
    if wt_factory.exists() or wt_factory.is_symlink():
        if wt_factory.is_dir() and not wt_factory.is_symlink():
            shutil.rmtree(wt_factory)
        else:
            wt_factory.unlink()

    wt_factory.mkdir(parents=True, exist_ok=True)

    for entry in _SHARED_SYMLINK_ENTRIES:
        src = factory_dir / entry
        if src.exists():
            (wt_factory / entry).symlink_to(src)

    for entry in _COPY_ENTRIES:
        src = factory_dir / entry
        if src.exists():
            dst = wt_factory / entry
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    (wt_factory / "strategy").mkdir(exist_ok=True)
    (wt_factory / "reviews").mkdir(exist_ok=True)
    (wt_factory / "state").mkdir(exist_ok=True)

    backlog_src = factory_dir / "strategy" / "backlog.md"
    if backlog_src.exists():
        shutil.copy2(backlog_src, wt_factory / "strategy" / "backlog.md")

    # Copy remaining plugin-created subdirectories not already handled.
    _handled = set(_SHARED_SYMLINK_ENTRIES) | set(_COPY_ENTRIES)
    if factory_dir.is_dir():
        for child in factory_dir.iterdir():
            if child.name in _handled or not child.is_dir():
                continue
            dst = wt_factory / child.name
            if not dst.exists():
                shutil.copytree(child, dst)

    log.info("worktree_created", branch=branch, path=str(wt_dir))

    try:
        from factory.events import emit_event

        emit_event(
            project_path,
            "worktree.created",
            data={
                "run_id": run_id,
                "worktree_path": str(wt_dir),
                "branch": branch,
                "base_branch": base_branch,
            },
        )
    except Exception:
        pass

    return wt_dir, branch


def create_experiment_worktree(
    project_path: Path,
    exp_id: int,
    base_commit: str,
) -> tuple[Path, str]:
    """Create an isolated worktree for a parallel experiment branch.

    Each worktree gets its own `.factory/` directory (not a symlink) seeded
    with read-only config from the project.  This ensures parallel branches
    write independent `last_eval.json` files so the selection node can
    compare genuinely separate scores.

    Returns (worktree_path, branch_name).
    """
    project_path = project_path.resolve()
    branch = f"factory/exp-{exp_id}"
    factory_dir = project_path / ".factory"
    wt_parent = project_path / ".factory-worktrees"
    wt_dir = wt_parent / f"exp-{exp_id}"

    log.info("experiment_worktree_create", branch=branch, base=base_commit[:12], exp_id=exp_id)

    wt_parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "-b", branch, base_commit],
        cwd=project_path,
        check=True,
        capture_output=True,
    )

    _seed_experiment_factory(factory_dir, wt_dir / ".factory")

    log.info("experiment_worktree_created", branch=branch, path=str(wt_dir))

    try:
        from factory.events import emit_event

        emit_event(
            project_path,
            "experiment_worktree.created",
            data={
                "exp_id": exp_id,
                "worktree_path": str(wt_dir),
                "branch": branch,
                "base_commit": base_commit,
            },
        )
    except Exception:
        pass

    return wt_dir, branch


def _seed_experiment_factory(source: Path, dest: Path) -> None:
    """Copy config entries from the project .factory/ into an experiment worktree.

    Only copies entries listed in _EXPERIMENT_SEED_ENTRIES so that mutable
    runtime state (results.tsv, experiments/, last_eval.json) stays independent.
    """
    if dest.is_symlink():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    if not source.is_dir():
        return

    for entry_name in _EXPERIMENT_SEED_ENTRIES:
        src = source / entry_name
        dst = dest / entry_name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _sync_backlog_to_main(worktree_path: Path, project_path: Path) -> None:
    """Sync backlog changes from worktree back to main .factory/."""
    wt_backlog = worktree_path / ".factory" / "strategy" / "backlog.md"
    main_backlog = project_path / ".factory" / "strategy" / "backlog.md"
    if wt_backlog.exists() and not wt_backlog.is_symlink():
        main_backlog.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wt_backlog, main_backlog)
        log.info("backlog_synced", src=str(wt_backlog), dst=str(main_backlog))


_BOOTSTRAP_FACTORY_FILES: Final[tuple[str, ...]] = (
    "config.json",
    "eval_profile.json",
)


def _sync_bootstrap_to_main(worktree_path: Path, project_path: Path) -> None:
    """Sync bootstrap artifacts from worktree back to main project.

    Only copies files that are real (not symlinks), meaning they were freshly
    created during this run rather than symlinked from main.
    """
    wt_factory = worktree_path / ".factory"
    main_factory = project_path / ".factory"

    if not wt_factory.exists():
        return

    main_factory.mkdir(parents=True, exist_ok=True)
    for filename in _BOOTSTRAP_FACTORY_FILES:
        src = wt_factory / filename
        if src.exists() and not src.is_symlink():
            dst = main_factory / filename
            if not dst.exists():
                shutil.copy2(src, dst)
                log.info("bootstrap_synced", file=filename, src=str(src), dst=str(dst))

    wt_factory_md = worktree_path / "factory.md"
    main_factory_md = project_path / "factory.md"
    if wt_factory_md.exists() and not wt_factory_md.is_symlink() and not main_factory_md.exists():
        shutil.copy2(wt_factory_md, main_factory_md)
        log.info("bootstrap_synced", file="factory.md", src=str(wt_factory_md), dst=str(main_factory_md))


def _preserve_telemetry(worktree_path: Path, project_path: Path) -> None:
    """Copy telemetry files from worktree .factory/ to main project .factory/."""
    wt_factory = worktree_path / ".factory"
    main_factory = project_path / ".factory"

    if not wt_factory.exists():
        return

    main_factory.mkdir(parents=True, exist_ok=True)
    for filename in _TELEMETRY_FILES:
        src = wt_factory / filename
        if src.exists():
            dst = main_factory / filename
            shutil.copy2(src, dst)
            log.info("telemetry_preserved", file=filename, src=str(src), dst=str(dst))


def _has_active_sessions(worktree_path: Path) -> bool:
    """Check if any Claude Code sessions are active in the worktree.

    Returns True if active sessions found, False otherwise.
    Fails open: returns False on any error so removal proceeds.
    """
    try:
        result = subprocess.run(
            ["claude", "agents", "--json", "--cwd", str(worktree_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        sessions = json.loads(result.stdout)
        if not isinstance(sessions, list):
            return False
        return any(
            isinstance(s, dict) and s.get("state") in ("working", "blocked") for s in sessions
        )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
        return False


def _should_remove_worktree(branch: str) -> bool:
    """Check whether a worktree should be removed based on config.

    Experiment branches (factory/exp-*) are always removed regardless of config.
    For run branches, consults FACTORY_REMOVE_WORKTREE (default: true).
    """
    if branch.startswith("factory/exp-"):
        return True

    from factory import user_config

    value = user_config.resolve(
        "remove_worktree", env_var="FACTORY_REMOVE_WORKTREE", default="true"
    )
    return (value or "true").lower() in ("true", "1", "yes")


def remove_worktree(project_path: Path, worktree_path: Path, branch: str) -> None:
    """Remove a worktree and its branch. Safe to call on already-removed paths."""
    log.info("worktree_remove", branch=branch, path=str(worktree_path))

    run_id = branch.removeprefix("factory/run-")

    if worktree_path.exists():
        if _has_active_sessions(worktree_path):
            log.warning(
                "worktree_remove_skipped",
                reason="active_sessions",
                path=str(worktree_path),
                branch=branch,
            )
            return
        if not _should_remove_worktree(branch):
            log.info(
                "worktree_remove_skipped",
                reason="retention_enabled",
                path=str(worktree_path),
                branch=branch,
            )
            try:
                from factory.events import emit_event

                emit_event(
                    project_path,
                    "worktree.retained",
                    data={
                        "run_id": run_id,
                        "branch": branch,
                        "worktree_path": str(worktree_path),
                    },
                )
            except Exception:
                pass
            import sys

            print(
                f"Worktree retained: {worktree_path}\n"
                f"To clean up: git worktree remove {worktree_path} && git branch -D {branch}",
                file=sys.stderr,
            )
            return
        _sync_backlog_to_main(worktree_path, project_path)
        _sync_bootstrap_to_main(worktree_path, project_path)
        _preserve_telemetry(worktree_path, project_path)
        shutil.rmtree(worktree_path)

    try:
        from factory.events import emit_event

        emit_event(
            project_path,
            "worktree.removed",
            data={
                "run_id": run_id,
                "branch": branch,
            },
        )
    except Exception:
        pass

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
                name = d.name
                if name.startswith("exp-"):
                    branch = f"factory/{name}"
                else:
                    branch = f"factory/run-{name.removeprefix('run-')}"
                    if not _should_remove_worktree(branch):
                        log.info("worktree_prune_skipped", reason="retention_enabled", name=name)
                        continue
                shutil.rmtree(d)
                pruned.append(f"Removed orphaned directory: {name}")
                log.info("worktree_pruned_orphan", name=name)
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=project_path,
                    capture_output=True,
                )

    if pruned:
        log.info("worktree_prune_complete", pruned_count=len(pruned))

    return pruned


def _is_unborn_repo(project_path: Path) -> bool:
    """Return True if the repo exists but has no commits (unborn HEAD)."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    return result.returncode != 0


def _bootstrap_unborn_repo(project_path: Path) -> None:
    """Create an initial empty commit so worktrees can branch from it."""
    log.info("bootstrap_unborn_repo", path=str(project_path))
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init (factory bootstrap)"],
        cwd=project_path,
        capture_output=True,
        check=True,
    )


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

    # Current branch (works on repos with commits)
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

    # Unborn repo: rev-parse fails but symbolic-ref still resolves HEAD
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        branch = result.stdout.strip()
        log.debug("detect_default_branch", source="symbolic_ref", branch=branch)
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
        line.split(" ", 1)[1] for line in result.stdout.splitlines() if line.startswith("worktree ")
    }
