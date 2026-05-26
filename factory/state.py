"""Project state detection — determines which mode the factory should operate in."""

import os
import subprocess
from pathlib import Path

import structlog

from factory.models import ProjectState

log = structlog.get_logger()

# Build/manifest files that mark a configured, real project.
_MANIFEST_FILES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Package.swift",
    "pom.xml",
    "build.gradle",
)

# Source extensions used to confirm a populated source tree.
_SOURCE_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".swift", ".java", ".rb",
)

# Directories never counted as project source.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "dist", "build", ".factory", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

# A repo with a manifest plus this many source files is clearly built.
_SOURCE_THRESHOLD = 3


def _has_substantial_source(project_path: Path) -> bool:
    """True if the repo has a manifest plus a populated source tree.

    Walks the tree (pruning vendored/build/hidden dirs) and stops as soon as the
    source-file threshold is reached, so this stays cheap even on large repos.
    """
    if not any((project_path / m).exists() for m in _MANIFEST_FILES):
        return False
    found = 0
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(_SOURCE_EXTENSIONS):
                found += 1
                if found >= _SOURCE_THRESHOLD:
                    return True
    return False


def _is_built_project(project_path: Path) -> bool:
    """True if the repo is already a real, built project.

    A committed ``factory.md`` marks a factory-managed project; substantial
    source marks any other built codebase. Either way the repo is *not* an
    unbuilt scaffold, so the open plan/implementation-issue heuristic — which
    keys off the factory's own ``implementation`` backlog label — must not
    classify it as ``REPO_INCOMPLETE`` (see issue #378).
    """
    if (project_path / "factory.md").exists():
        log.debug("built_project", reason="factory_md")
        return True
    if _has_substantial_source(project_path):
        log.debug("built_project", reason="substantial_source")
        return True
    return False


def _has_open_plan_issues(project_path: Path) -> bool:
    """Check GitHub for open issues with 'plan' or 'implementation' labels."""
    for label in ("plan", "implementation"):
        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--label", label, "--state", "open", "--json", "number"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip() not in ("", "[]"):
                log.debug("open_plan_issues_found", label=label)
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.debug("open_plan_issues_check_failed", label=label)
            return False
    return False


def _has_pending_eval_review(project_path: Path) -> bool:
    """Check if evals exist but haven't been human-reviewed.

    The factory is in EVALS_PENDING_REVIEW when:
    - .factory/config.json exists
    - .factory/eval_profile.json exists with human_reviewed=False
    """
    import json

    profile_path = project_path / ".factory" / "eval_profile.json"
    if not profile_path.exists():
        return False

    try:
        data = json.loads(profile_path.read_text())
        pending = data.get("human_reviewed", False) is False
        log.debug("pending_eval_review_check", human_reviewed=data.get("human_reviewed"), pending=pending)
        return pending
    except (json.JSONDecodeError, KeyError):
        log.debug("pending_eval_review_parse_error", path=str(profile_path))
        return False


def detect_state(project_path: Path) -> ProjectState:
    """Determine which of the 5 project states applies to a given path.

    Logic:
      1. Path doesn't exist or has no .git -> NO_REPO
      2. eval_profile.json exists with human_reviewed=False -> EVALS_PENDING_REVIEW
      3. .factory/config.json exists -> HAS_FACTORY
      4. Not a built project, open plan/implementation GitHub issues -> REPO_INCOMPLETE
      5. Otherwise -> NO_FACTORY

    A built/factory-managed repo (committed ``factory.md`` or substantial source)
    is never REPO_INCOMPLETE: the open-issue heuristic keys off the factory's own
    ``implementation`` backlog label, which a mature repo accumulates (issue #378).
    """
    log.debug("detect_state_start", project=str(project_path))

    if not project_path.exists() or not (project_path / ".git").exists():
        log.info("detect_state_result", state=ProjectState.NO_REPO.value)
        return ProjectState.NO_REPO

    # Check for pending eval review BEFORE checking for full factory.
    # This handles the discover → review → init flow where eval_profile.json
    # exists but config.json does not yet.
    if _has_pending_eval_review(project_path):
        log.info("detect_state_result", state=ProjectState.EVALS_PENDING_REVIEW.value)
        return ProjectState.EVALS_PENDING_REVIEW

    if (project_path / ".factory" / "config.json").exists():
        log.info("detect_state_result", state=ProjectState.HAS_FACTORY.value)
        return ProjectState.HAS_FACTORY

    # A built/factory-managed repo is never "incomplete" — skip the open-issue
    # heuristic (and its network call) entirely. Only an unbuilt scaffold with
    # open planning issues routes to Build mode.
    if not _is_built_project(project_path) and _has_open_plan_issues(project_path):
        log.info("detect_state_result", state=ProjectState.REPO_INCOMPLETE.value)
        return ProjectState.REPO_INCOMPLETE

    log.info("detect_state_result", state=ProjectState.NO_FACTORY.value)
    return ProjectState.NO_FACTORY
