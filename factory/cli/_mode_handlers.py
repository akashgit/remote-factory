"""Mode-specific early-exit handlers for CEO commands (review, deep-qa)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factory.cli._helpers import _print_banner, _resolve_runner, _run


def _resolve_model(args: argparse.Namespace) -> str | None:
    """Resolve model: CLI flag > FACTORY_MODEL env var > config.toml > None."""
    from factory.user_config import resolve

    flag = (getattr(args, "model", None) or "").strip() or None
    return resolve("model", cli_value=flag, env_var="FACTORY_MODEL")


def _resolve_tmux_persist(args: argparse.Namespace) -> bool:
    """Resolve tmux_persist: CLI flag > FACTORY_TMUX_PERSIST env var > config.toml > False."""
    from factory.user_config import resolve

    cli_flag = getattr(args, "tmux_persist", False)
    cli_value = "true" if cli_flag else None
    val = resolve(
        "tmux_persist", cli_value=cli_value, env_var="FACTORY_TMUX_PERSIST", default="false"
    )
    return bool(val and val.lower() in ("1", "true", "yes"))


def _resolve_background(args: argparse.Namespace) -> bool:
    """Resolve background: CLI flag > FACTORY_BG env var > config.toml > False."""
    from factory.user_config import resolve

    cli_flag = getattr(args, "bg", False)
    cli_value = "true" if cli_flag else None
    val = resolve("bg", cli_value=cli_value, env_var="FACTORY_BG", default="false")
    return bool(val and val.lower() in ("1", "true", "yes"))


def _resolve_bg_agents(args: argparse.Namespace) -> bool:
    """Resolve bg_agents: CLI flag > FACTORY_BG_AGENTS env var > config.toml > False."""
    from factory.user_config import resolve

    cli_flag = getattr(args, "bg_agents", False)
    cli_value = "true" if cli_flag else None
    val = resolve("bg_agents", cli_value=cli_value, env_var="FACTORY_BG_AGENTS", default="false")
    return bool(val and val.lower() in ("1", "true", "yes"))


def _auto_detect_mode(
    project_path: Path, has_prompt: bool = False, force_fresh: bool = False
) -> str:
    """Detect the right mode based on project state.

    Checks for an in-flight cycle first — if one exists, returns its mode
    regardless of current project state (prevents mode flip on respawn).

    Args:
        project_path: Path to the project.
        has_prompt: True if a build spec is available.
        force_fresh: If True, ignores in-flight cycle and detects from scratch.
    """
    import structlog

    from factory.ceo_completion import read_cycle_state
    from factory.cli._helpers import DEAD_MODES
    from factory.models import ProjectState
    from factory.state import detect_state

    _log = structlog.get_logger()

    if not force_fresh:
        cycle_state = read_cycle_state(project_path)
        if cycle_state:
            mode = cycle_state.mode
            if mode in DEAD_MODES:
                new_mode = DEAD_MODES[mode]
                _log.warning("cycle_state.mode_migrated", old=mode, new=new_mode)
                mode = new_mode
            print(
                f"  In-flight cycle: {cycle_state.cycle_id} → mode: {mode} "
                f"(respawns: {cycle_state.respawns})",
                file=sys.stderr,
            )
            return mode

    state = detect_state(project_path)
    mode_map = {
        ProjectState.NO_REPO: "design",
        ProjectState.REPO_INCOMPLETE: "design",
        ProjectState.NO_FACTORY: "design",
        ProjectState.EVALS_PENDING_REVIEW: "design",
        ProjectState.HAS_FACTORY: "design",
    }
    mode = mode_map[state]

    print(f"  State: {state.value} → mode: {mode}", file=sys.stderr)
    return mode


def handle_review_mode(
    args: argparse.Namespace,
    raw_path: str,
    headless: bool,
) -> int:
    """Process --mode review. Returns exit code."""
    from factory.agents.runner import resolve_prompt
    from factory.runners import get_runner

    pr_number = getattr(args, "pr", None)
    if pr_number is None:
        print("Error: --mode review requires --pr <number>", file=sys.stderr)
        return 1

    repo = getattr(args, "repo", None)
    model = _resolve_model(args)
    runner_name = _resolve_runner(args)

    project_path = Path(raw_path).expanduser().resolve()
    if not project_path.is_dir():
        print(
            f"Error: project path must be an existing directory for review mode: {raw_path}",
            file=sys.stderr,
        )
        return 1

    _print_banner("review")

    repo_flag = f" --repo {repo}" if repo else ""
    repo_clause = f" in repo `{repo}`" if repo else ""
    task = (
        f"Project: {project_path}\nMode: review\n\n"
        f"## PR Review Directive\n\n"
        f"Review PR #{pr_number}{repo_clause}.\n\n"
        f"This is a review-only run — no experiment lifecycle, no Builder iterations.\n\n"
        f"Execute these steps:\n"
        f"1. Run baseline eval (factory eval) to get $SCORE_BEFORE\n"
        f"2. Run the deep-QA pipeline (health_checker, code_reviewer, adversarial_tester) — "
        f"single pass, iteration 1/1, no Builder fix loop\n"
        f"3. Run Hard Precheck Gate\n"
        f"4. Post verdict via "
        f"factory review --verdict <KEEP|REVERT> --pr {pr_number} "
        f'--reason "$REASON" '
        f"--qa-body-file .factory/reviews/adversarial-qa.md"
        f"{repo_flag}\n"
        f"\nSet $REASON to the QA verdict summary (e.g. 'QA: CLEAN — 2854 tests pass, 0 issues' "
        f"or 'QA: ISSUES_FOUND — 3 critical issues'). Set $VERDICT to KEEP if QA is CLEAN, "
        f"REVERT otherwise.\n"
    )

    from factory.skill_cache import ensure_skills

    ensure_skills(project_path)

    if not headless:
        from factory.models import AgentRunRequest

        prompt = resolve_prompt("ceo", project_path)
        runner = get_runner(runner_name)
        return runner.interactive_run(
            AgentRunRequest(
                prompt=prompt,
                task=task,
                cwd=project_path,
                model=model,
                role="ceo",
                skip_permissions=True,
            )
        )

    from factory.ceo_completion import run_ceo_with_completion_guard

    result, code = _run(
        run_ceo_with_completion_guard(
            project_path,
            task,
            mode="review",
            runner_name=runner_name,
            model=model,
            timeout=7200.0,
            max_respawns=1,
        )
    )
    print(result)
    return code


def handle_deep_qa_mode(
    args: argparse.Namespace,
    raw_path: str,
    headless: bool,
) -> int:
    """Process --mode deep-qa. Returns exit code."""
    from factory.agents.runner import (
        begin_cycle_session,
        complete_cycle_session,
        resolve_prompt,
    )
    from factory.runners import get_runner

    pr_number = getattr(args, "pr", None)
    if pr_number is None:
        print("Error: --mode deep-qa requires --pr <number>", file=sys.stderr)
        return 1

    repo = getattr(args, "repo", None)
    model = _resolve_model(args)
    runner_name = _resolve_runner(args)

    project_path = Path(raw_path).expanduser().resolve()
    if not project_path.is_dir():
        print(
            f"Error: project path must be an existing directory for deep-qa mode: {raw_path}",
            file=sys.stderr,
        )
        return 1

    _print_banner("deep-qa")

    repo_flag = f" --repo {repo}" if repo else ""
    repo_clause = f" in repo `{repo}`" if repo else ""
    task = (
        f"Project: {project_path}\nMode: deep-qa\n\n"
        f"## Deep-QA Verification Directive\n\n"
        f"Run the deep-QA verification pipeline for PR #{pr_number}{repo_clause}.\n\n"
        f"Execute the 3-specialist pipeline:\n"
        f"1. health_checker — run eval, compare scores, write health-check.md\n"
        f"2. code_reviewer — 7-category code review, write code-review.md\n"
        f"3. adversarial_tester — skeptical feature testing, write adversarial-qa.md\n\n"
        f"Key parameters:\n"
        f"- PR_NUMBER={pr_number}\n"
        f"- PROJECT_PATH={project_path}\n"
        f"{f'- REPO={repo}' + chr(10) if repo else ''}"
        f"\nPost the final verdict via:\n"
        f"factory review --verdict <KEEP|REVERT> --pr {pr_number} "
        f'--reason "$REASON" '
        f"--qa-body-file .factory/reviews/adversarial-qa.md"
        f"{repo_flag}\n"
        f"\nSet $REASON to the QA verdict summary (e.g. 'QA: CLEAN — 2854 tests pass, 0 issues' "
        f"or 'QA: ISSUES_FOUND — 3 critical issues'). Set $VERDICT to KEEP if QA is CLEAN, "
        f"REVERT otherwise.\n"
        f"\nIMPORTANT: Do NOT post any PR comments (gh pr comment, gh issue comment). "
        f"The factory review command above is the ONLY GitHub output artifact.\n"
    )

    cycle_span_id = begin_cycle_session(project_path, cycle_id="deep-qa", model=model)

    from factory.skill_cache import ensure_skills

    ensure_skills(project_path)

    if not headless:
        from factory.models import AgentRunRequest

        prompt = resolve_prompt("ceo", project_path)
        runner = get_runner(runner_name)
        rc = runner.interactive_run(
            AgentRunRequest(
                prompt=prompt,
                task=task,
                cwd=project_path,
                model=model,
                role="ceo",
                skip_permissions=True,
            )
        )
        complete_cycle_session(project_path, cycle_span_id)
        return rc

    from factory.ceo_completion import run_ceo_with_completion_guard

    result, code = _run(
        run_ceo_with_completion_guard(
            project_path,
            task,
            mode="deep-qa",
            runner_name=runner_name,
            model=model,
            timeout=7200.0,
            max_respawns=1,
        )
    )
    complete_cycle_session(project_path, cycle_span_id)
    print(result)
    return code
