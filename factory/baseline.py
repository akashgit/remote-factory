"""Retrieve stored eval baselines from the benchmark-data branch.

The CI pipeline (eval.yml) appends eval results to eval-results.jsonl on the
benchmark-data branch after every push to main.  This module reads that file
to look up the baseline eval for a given commit SHA.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

from factory.models import CompositeScore, EvalResult

log = structlog.get_logger()

EVAL_RESULTS_FILE = "eval-results.jsonl"
DATA_BRANCH = "benchmark-data"


def _read_jsonl_from_branch(project_path: Path) -> str | None:
    """Read eval-results.jsonl from the benchmark-data branch via git."""
    try:
        result = subprocess.run(
            ["git", "show", f"{DATA_BRANCH}:{EVAL_RESULTS_FILE}"],
            capture_output=True, text=True, timeout=30,
            cwd=project_path,
        )
        if result.returncode == 0:
            return result.stdout
        log.debug("git_show_failed", branch=DATA_BRANCH, file=EVAL_RESULTS_FILE,
                  stderr=result.stderr.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _read_jsonl_from_remote(repo: str) -> str | None:
    """Read eval-results.jsonl from remote via gh api."""
    try:
        result = subprocess.run(
            ["gh", "api",
             f"repos/{repo}/contents/{EVAL_RESULTS_FILE}?ref={DATA_BRANCH}",
             "--jq", ".content"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            import base64
            return base64.b64decode(result.stdout.strip()).decode()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _detect_repo(project_path: Path) -> str | None:
    """Detect GitHub owner/repo from git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
            cwd=project_path,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        if "github.com" in url:
            # Handle both HTTPS and SSH URLs
            if url.startswith("git@"):
                # git@github.com:owner/repo.git
                path = url.split(":", 1)[1]
            else:
                # https://github.com/owner/repo.git
                path = url.split("github.com/", 1)[1]
            return path.removesuffix(".git")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _find_base_commit(project_path: Path, target_branch: str = "main") -> str | None:
    """Find the base commit of the current branch relative to target."""
    try:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", target_branch],
            capture_output=True, text=True, timeout=10,
            cwd=project_path,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    for remote_target in [f"origin/{target_branch}", target_branch]:
        try:
            result = subprocess.run(
                ["git", "merge-base", "HEAD", remote_target],
                capture_output=True, text=True, timeout=10,
                cwd=project_path,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return None


def _parse_jsonl(content: str) -> list[dict]:
    """Parse JSONL content into a list of dicts, skipping bad lines."""
    entries = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _find_entry_for_commit(entries: list[dict], commit: str) -> dict | None:
    """Find the latest eval entry matching a commit SHA.

    Supports both full SHA and prefix matching.
    """
    matches = [
        e for e in entries
        if (stored := e.get("commit", ""))
        and (stored.startswith(commit) or commit.startswith(stored))
    ]
    if not matches:
        return None
    return matches[-1]


def _find_latest_main_entry(entries: list[dict]) -> dict | None:
    """Find the most recent entry from the main branch."""
    main_entries = [
        e for e in entries
        if e.get("ref") in ("refs/heads/main", "refs/heads/master")
    ]
    if not main_entries:
        return None
    return main_entries[-1]


def _entry_to_composite(entry: dict) -> CompositeScore:
    """Convert a JSONL entry to a CompositeScore."""
    results = [
        EvalResult(
            name=r["name"],
            score=r["score"],
            weight=r["weight"],
            passed=r["passed"],
            details=r.get("details", ""),
        )
        for r in entry.get("results", [])
    ]
    return CompositeScore(
        total=entry.get("total", 0.0),
        results=results,
        guard_violations=entry.get("guard_violations", []),
        passed=entry.get("passed", False),
    )


def get_baseline(
    project_path: Path,
    commit: str | None = None,
    repo: str | None = None,
    target_branch: str = "main",
) -> CompositeScore | None:
    """Retrieve the stored eval baseline for a commit.

    Args:
        project_path: Path to the project root (must be a git repo).
        commit: Specific commit SHA to look up. If None, auto-detects the
            base commit of the current branch relative to target_branch.
        repo: GitHub owner/repo string.  If None, auto-detected from remote.
        target_branch: Branch to compute merge-base against (default: main).

    Returns:
        CompositeScore if a baseline is found, None otherwise.
    """
    if commit is None:
        commit = _find_base_commit(project_path, target_branch)
        if commit is None:
            log.warning("baseline_no_base_commit", target_branch=target_branch)
            return None
    log.debug("baseline_lookup", commit=commit[:12])

    content = _read_jsonl_from_branch(project_path)

    if content is None and repo is None:
        repo = _detect_repo(project_path)

    if content is None and repo:
        log.debug("baseline_trying_remote", repo=repo)
        content = _read_jsonl_from_remote(repo)

    if content is None:
        log.info("baseline_no_data", commit=commit[:12])
        return None

    entries = _parse_jsonl(content)
    if not entries:
        log.info("baseline_empty_data")
        return None

    entry = _find_entry_for_commit(entries, commit)
    if entry is None:
        log.info("baseline_commit_not_found", commit=commit[:12], total_entries=len(entries))
        return None

    log.info("baseline_found", commit=commit[:12], total=entry.get("total"))
    return _entry_to_composite(entry)


def get_latest_main_baseline(
    project_path: Path,
    repo: str | None = None,
) -> CompositeScore | None:
    """Retrieve the most recent eval baseline from the main branch.

    Useful for PR comparisons where you want the latest main state,
    not necessarily the merge-base commit.
    """
    content = _read_jsonl_from_branch(project_path)

    if content is None and repo is None:
        repo = _detect_repo(project_path)
    if content is None and repo:
        content = _read_jsonl_from_remote(repo)

    if content is None:
        return None

    entries = _parse_jsonl(content)
    if not entries:
        return None

    entry = _find_latest_main_entry(entries)
    if entry is None:
        return None

    return _entry_to_composite(entry)
