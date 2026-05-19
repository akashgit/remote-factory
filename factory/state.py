"""Project state detection — determines which mode the factory should operate in."""

from pathlib import Path

import structlog

from factory.models import ProjectState

log = structlog.get_logger()


def _has_open_plan_issues(project_path: Path) -> bool:
    """Check GitHub/GitLab for open issues with 'plan' or 'implementation' labels."""
    try:
        from factory.forge import ForgeOps
        ops = ForgeOps(project_path)
    except (RuntimeError, FileNotFoundError):
        log.debug("open_plan_issues_forge_detection_failed")
        return False

    for label in ("plan", "implementation"):
        issues = ops.issue_list(state="open", labels=[label], limit=5, fields=["number"])
        if issues:
            log.debug("open_plan_issues_found", label=label, forge=ops.forge)
            return True
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
      4. Has .git, open plan/implementation GitHub issues -> REPO_INCOMPLETE
      5. Has .git, no open issues -> NO_FACTORY
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

    if _has_open_plan_issues(project_path):
        log.info("detect_state_result", state=ProjectState.REPO_INCOMPLETE.value)
        return ProjectState.REPO_INCOMPLETE

    log.info("detect_state_result", state=ProjectState.NO_FACTORY.value)
    return ProjectState.NO_FACTORY
