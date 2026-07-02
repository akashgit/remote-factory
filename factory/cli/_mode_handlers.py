"""Mode-specific early-exit handlers for CEO commands (review, qa)."""
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
    val = resolve("tmux_persist", cli_value=cli_value, env_var="FACTORY_TMUX_PERSIST", default="false")
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


def _auto_detect_mode(project_path: Path, has_prompt: bool = False, force_fresh: bool = False) -> str:
    """Detect the right mode based on project state.

    Checks for an in-flight cycle first -- if one exists, returns its mode
    regardless of current project state (prevents mode flip on respawn).
    """
    import sys

    from factory.ceo_completion import read_cycle_state
    from factory.models import ProjectState
    from factory.state import detect_state

    from factory.cli._path_resolver import _has_research_target

    if not force_fresh:
        cycle_state = read_cycle_state(project_path)
        if cycle_state:
            print(
                f"  In-flight cycle: {cycle_state.cycle_id} → mode: {cycle_state.mode} "
                f"(respawns: {cycle_state.respawns})",
                file=sys.stderr,
            )
            return cycle_state.mode

    state = detect_state(project_path)
    mode_map = {
        ProjectState.NO_REPO: "build",
        ProjectState.REPO_INCOMPLETE: "build",
        ProjectState.NO_FACTORY: "build" if has_prompt else "discover",
        ProjectState.EVALS_PENDING_REVIEW: "discover",
        ProjectState.HAS_FACTORY: "improve",
    }
    mode = mode_map[state]

    if state == ProjectState.HAS_FACTORY and _has_research_target(project_path):
        mode = "research"

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
        print(f"Error: project path must be an existing directory for review mode: {raw_path}",
              file=sys.stderr)
        return 1

    _print_banner("review")

    repo_flag = f" --repo {repo}" if repo else ""
    repo_clause = f" in repo `{repo}`" if repo else ""
    task = (
        f"Project: {project_path}\nMode: review\n\n"
        f"## PR Review Directive\n\n"
        f"Review PR #{pr_number}{repo_clause}.\n\n"
        f"This is a review-only run — no experiment lifecycle, no Builder iterations.\n\n"
        f"Execute these Improve pipeline steps:\n"
        f"1. Run baseline eval (factory eval) to get $SCORE_BEFORE\n"
        f"2. Run step 2c-qa (QA Agent Verification) — single pass, "
        f"iteration 1/1, no Builder fix loop\n"
        f"3. Run step 2d (Hard Precheck Gate)\n"
        f"4. Post verdict via "
        f"factory review --verdict <KEEP|REVERT> --pr {pr_number} "
        f"--reason \"$REASON\" "
        f"--qa-body-file .factory/reviews/qa-latest.md"
        f"{repo_flag}\n"
        f"\nSet $REASON to the QA verdict summary (e.g. 'QA: CLEAN — 2854 tests pass, 0 issues' "
        f"or 'QA: ISSUES_FOUND — 3 critical issues'). Set $VERDICT to KEEP if QA is CLEAN, REVERT otherwise.\n"
    )

    if not headless:
        from factory.models import AgentRunRequest

        prompt = resolve_prompt("ceo", project_path)
        runner = get_runner(runner_name)
        return runner.interactive_run(AgentRunRequest(
            prompt=prompt, task=task, cwd=project_path,
            model=model, role="ceo", skip_permissions=True,
        ))

    from factory.ceo_completion import run_ceo_with_completion_guard
    result, code = _run(run_ceo_with_completion_guard(
        project_path,
        task,
        mode="review",
        runner_name=runner_name,
        model=model,
        timeout=7200.0,
        max_respawns=1,
    ))
    print(result)
    return code


def handle_qa_mode(
    args: argparse.Namespace,
    raw_path: str,
    headless: bool,
) -> int:
    """Process --mode qa. Returns exit code."""
    from factory.agents.runner import resolve_prompt
    from factory.runners import get_runner

    pr_number = getattr(args, "pr", None)
    if pr_number is None:
        print("Error: --mode qa requires --pr <number>", file=sys.stderr)
        return 1

    repo = getattr(args, "repo", None)
    model = _resolve_model(args)
    runner_name = _resolve_runner(args)

    project_path = Path(raw_path).expanduser().resolve()
    if not project_path.is_dir():
        print(f"Error: project path must be an existing directory for qa mode: {raw_path}",
              file=sys.stderr)
        return 1

    _print_banner("qa")

    repo_flag = f" --repo {repo}" if repo else ""
    repo_clause = f" in repo `{repo}`" if repo else ""
    task = (
        f"Project: {project_path}\nMode: qa\n\n"
        f"## QA Verification Directive\n\n"
        f"Run the QA verification pipeline for PR #{pr_number}{repo_clause}.\n\n"
        f"Read and follow the workflow-qa SKILL.md playbook at "
        f"skills/workflow-qa/SKILL.md.\n\n"
        f"Key parameters:\n"
        f"- PR_NUMBER={pr_number}\n"
        f"- PROJECT_PATH={project_path}\n"
        f"{f'- REPO={repo}' + chr(10) if repo else ''}"
        f"\nPost the final verdict via:\n"
        f"factory review --verdict <KEEP|REVERT> --pr {pr_number} "
        f"--reason \"$REASON\" "
        f"--qa-body-file .factory/reviews/qa-latest.md"
        f"{repo_flag}\n"
        f"\nSet $REASON to the QA verdict summary (e.g. 'QA: CLEAN — 2854 tests pass, 0 issues' "
        f"or 'QA: ISSUES_FOUND — 3 critical issues'). Set $VERDICT to KEEP if QA is CLEAN, REVERT otherwise.\n"
        f"\nIMPORTANT: Do NOT post any PR comments (gh pr comment, gh issue comment). "
        f"The factory review command above is the ONLY GitHub output artifact.\n"
    )

    if not headless:
        from factory.models import AgentRunRequest

        prompt = resolve_prompt("ceo", project_path)
        runner = get_runner(runner_name)
        return runner.interactive_run(AgentRunRequest(
            prompt=prompt, task=task, cwd=project_path,
            model=model, role="ceo", skip_permissions=True,
        ))

    from factory.ceo_completion import run_ceo_with_completion_guard
    result, code = _run(run_ceo_with_completion_guard(
        project_path,
        task,
        mode="qa",
        runner_name=runner_name,
        model=model,
        timeout=7200.0,
        max_respawns=1,
    ))
    print(result)
    return code
