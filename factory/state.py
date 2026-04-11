"""Project state detection — determines which mode the factory should operate in."""

import subprocess
from pathlib import Path

from factory.models import ProjectState


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
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
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
        return data.get("human_reviewed", False) is False
    except (json.JSONDecodeError, KeyError):
        return False


def detect_state(project_path: Path) -> ProjectState:
    """Determine which of the 5 project states applies to a given path.

    Logic:
      1. Path doesn't exist or has no .git -> NO_REPO
      2. eval_profile.json exists with human_reviewed=False -> EVALS_PENDING_REVIEW
      3. .factory/config.json exists -> HAS_FACTORY
      4. Has .git, open plan/implementation GitHub issues -> REPO_INCOMPLETE
      5. Has .git, no open issues -> NO_FACTORY
    """
    if not project_path.exists() or not (project_path / ".git").exists():
        return ProjectState.NO_REPO

    # Check for pending eval review BEFORE checking for full factory.
    # This handles the discover → review → init flow where eval_profile.json
    # exists but config.json does not yet.
    if _has_pending_eval_review(project_path):
        return ProjectState.EVALS_PENDING_REVIEW

    if (project_path / ".factory" / "config.json").exists():
        return ProjectState.HAS_FACTORY

    if _has_open_plan_issues(project_path):
        return ProjectState.REPO_INCOMPLETE

    return ProjectState.NO_FACTORY
