"""Project state detection — determines which mode the factory should operate in."""

import asyncio
import subprocess
from pathlib import Path

import structlog

from factory.models import ProjectState

log = structlog.get_logger()


def _has_open_plan_issues(project_path: Path) -> bool:
    """Check GitHub for open issues with the 'plan' label."""
    # NOTE: only 'plan' (an external scaffold convention) signals a genuinely
    # unbuilt repo. Do NOT add 'implementation' — that is the factory's OWN
    # backlog label, created by the CEO on already-built repos during Improve
    # mode, so an open 'implementation' issue means the opposite of "unbuilt".
    for label in ("plan",):
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
      4. Has .git, open 'plan' GitHub issues -> REPO_INCOMPLETE
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

    # Auto-bootstrap: if factory.md exists but config.json is missing,
    # regenerate config.json from factory.md without running discovery.
    factory_dir = project_path / ".factory"
    factory_md = project_path / "factory.md"
    if factory_md.exists() and not (factory_dir / "config.json").exists():
        try:
            from factory.store import ExperimentStore, ensure_factory_dir

            ensure_factory_dir(factory_dir)
            store = ExperimentStore(project_path)
            asyncio.run(store.reparse_config())
            log.info("auto_bootstrapped_from_factory_md", project=str(project_path))
        except Exception:
            log.warning(
                "auto_bootstrap_failed",
                project=str(project_path),
                hint="factory.md exists but could not regenerate config.json",
            )

    if (factory_dir / "config.json").exists():
        log.info("detect_state_result", state=ProjectState.HAS_FACTORY.value)
        return ProjectState.HAS_FACTORY

    if factory_dir.exists():
        log.warning(
            "factory_dir_without_config",
            factory_dir=str(factory_dir),
            hint="Run 'factory init' to generate config.json from factory.md",
        )

    if _has_open_plan_issues(project_path):
        log.info("detect_state_result", state=ProjectState.REPO_INCOMPLETE.value)
        return ProjectState.REPO_INCOMPLETE

    log.info("detect_state_result", state=ProjectState.NO_FACTORY.value)
    return ProjectState.NO_FACTORY
