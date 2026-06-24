"""Fetch stored eval baselines from the eval-data branch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _parse_scores_jsonl(raw: str) -> dict[str, dict]:
    """Parse scores.jsonl content into a {commit: record} lookup dict.

    Malformed lines are silently skipped.
    """
    lookup: dict[str, dict] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        commit = record.get("commit")
        if commit:
            lookup[commit] = record
    return lookup


def fetch_baseline(
    project_path: Path,
    commit_sha: str | None = None,
    remote: str = "origin",
    branch: str = "eval-data",
) -> dict | None:
    """Fetch the stored eval baseline for a commit.

    1. ``git fetch <remote> <branch>``
    2. Read ``scores.jsonl`` via ``git show``
    3. Exact SHA match first
    4. Ancestor walk via ``git rev-list`` (up to 50 commits)

    Returns the baseline record dict, or ``None`` if no match is found.
    """
    refspec = f"{branch}:refs/remotes/{remote}/{branch}"
    fetch_result = _git(["fetch", remote, refspec], cwd=project_path)
    if fetch_result.returncode != 0:
        log.warning("baseline.fetch_failed", remote=remote, branch=branch,
                    stderr=fetch_result.stderr.strip())
        return None

    show_result = _git(
        ["show", f"{remote}/{branch}:scores.jsonl"],
        cwd=project_path,
    )
    if show_result.returncode != 0:
        log.warning("baseline.show_failed", ref=f"{remote}/{branch}:scores.jsonl",
                    stderr=show_result.stderr.strip())
        return None

    lookup = _parse_scores_jsonl(show_result.stdout)
    if not lookup:
        return None

    sha = commit_sha or ""

    if sha in lookup:
        return lookup[sha]

    if sha:
        rev_list = _git(
            ["rev-list", sha, "--max-count=50"],
            cwd=project_path,
        )
        if rev_list.returncode == 0:
            for ancestor in rev_list.stdout.strip().splitlines():
                ancestor = ancestor.strip()
                if ancestor in lookup:
                    return lookup[ancestor]

    return None
