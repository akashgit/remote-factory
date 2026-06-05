"""Clean PR mode — strip non-essential artifacts from PRs before pushing to external repos."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()

DEFAULT_EXCLUDES = [
    "eval/score.py",
    "benchmarks/**",
    "tests/eval_*",
    ".factory/**",
]


def _glob_match(filepath: str, pattern: str) -> bool:
    """Match a filepath against a glob pattern, supporting ** for recursive matching."""
    if "**" in pattern:
        parts = pattern.split("**", 1)
        prefix, suffix = parts
        prefix = prefix.rstrip("/")
        suffix = suffix.lstrip("/")

        if prefix and not filepath.startswith(prefix + "/"):
            return False

        remaining = filepath[len(prefix):].lstrip("/") if prefix else filepath

        if suffix:
            return fnmatch.fnmatch(remaining, suffix) or fnmatch.fnmatch(
                remaining, "*/" + suffix
            )
        return True

    fp_parts = filepath.split("/")
    pat_parts = pattern.split("/")
    if len(fp_parts) != len(pat_parts):
        return False
    return all(fnmatch.fnmatch(fp, pat) for fp, pat in zip(fp_parts, pat_parts))


def filter_pr_diff(
    changed_files: list[str],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Apply include/exclude glob patterns to determine which files to keep.

    Returns (keep, strip) — two lists of file paths.

    Logic:
    - If include is non-empty, only files matching at least one include pattern survive.
    - Exclude patterns (merged with DEFAULT_EXCLUDES) remove files from the keep set.
    - A file matched by both include and exclude is excluded (exclude wins).
    """
    effective_exclude = list(DEFAULT_EXCLUDES)
    if exclude:
        effective_exclude.extend(exclude)

    keep: list[str] = []
    strip: list[str] = []

    for f in changed_files:
        excluded = any(_glob_match(f, pat) for pat in effective_exclude)
        if excluded:
            strip.append(f)
            continue

        if include:
            included = any(_glob_match(f, pat) for pat in include)
            if not included:
                strip.append(f)
                continue

        keep.append(f)

    log.debug(
        "filter_pr_diff",
        total=len(changed_files),
        keep=len(keep),
        strip=len(strip),
    )
    return keep, strip


def strip_pr_artifacts(
    project_path: Path,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    base_branch: str = "main",
    exp_id: int | None = None,
) -> tuple[list[str], list[str]]:
    """Create a cleaned commit removing non-essential files.

    Preserves the full diff in .factory/experiments/ before stripping.
    Returns (keep, stripped) file lists.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", base_branch],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        log.warning(
            "strip_pr_artifacts_diff_failed",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
        return [], []
    changed_files = [f for f in result.stdout.strip().splitlines() if f]

    if not changed_files:
        log.info("strip_pr_artifacts_no_changes")
        return [], []

    if exp_id is not None:
        exp_dir = project_path / ".factory" / "experiments" / f"{exp_id:03d}"
        if exp_dir.exists():
            full_diff = subprocess.run(
                ["git", "diff", base_branch],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if full_diff.returncode != 0:
                log.warning(
                    "strip_pr_artifacts_archive_diff_failed",
                    returncode=full_diff.returncode,
                    stderr=full_diff.stderr.strip(),
                )
            else:
                (exp_dir / "changes_full.diff").write_text(full_diff.stdout)
                log.info("strip_pr_artifacts_archived_full_diff", exp_id=exp_id)

    keep, stripped = filter_pr_diff(changed_files, include=include, exclude=exclude)

    if not stripped:
        log.info("strip_pr_artifacts_nothing_to_strip")
        return keep, []

    staged_files: list[str] = []
    for f in stripped:
        exists_on_base = subprocess.run(
            ["git", "cat-file", "-e", f"{base_branch}:{f}"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if exists_on_base.returncode == 0:
            res = subprocess.run(
                ["git", "checkout", base_branch, "--", f],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        else:
            res = subprocess.run(
                ["git", "rm", "-f", "--", f],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        if res.returncode != 0:
            log.warning(
                "strip_pr_artifacts_file_failed",
                file=f,
                returncode=res.returncode,
                stderr=res.stderr.strip(),
            )
            continue
        staged_files.append(f)

    if staged_files:
        subprocess.run(
            ["git", "add", "--"] + staged_files,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        commit_result = subprocess.run(
            ["git", "commit", "-m", "factory: clean PR artifacts"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if commit_result.returncode != 0:
            log.warning(
                "strip_pr_artifacts_commit_failed",
                returncode=commit_result.returncode,
                stderr=commit_result.stderr.strip(),
            )
            return keep, []

    log.info(
        "strip_pr_artifacts_complete",
        kept=len(keep),
        stripped=len(stripped),
        staged=len(staged_files),
    )
    return keep, stripped
