"""CEO flag validation, project resolution, and execution logic."""
from __future__ import annotations

import argparse
import json
import os
import structlog
import sys
import time
from pathlib import Path

from factory.cli._ceo_dispatch import _start_ceo_tailer, _stop_ceo_tailer
from factory.cli._helpers import (
    _ensure_dashboard,
    _print_banner,
    _read_target_branch,
    _resolve_runner,
    _run,
    _safe_is_dir,
    _safe_is_file,
)
from factory.cli._mode_handlers import (
    _resolve_background,
    _resolve_bg_agents,
    _resolve_model,
    _resolve_tmux_persist,
)
from factory.cli._path_resolver import (
    _dedupe_project_path,
    _derive_session_name,
    _extract_project_name,
    _get_projects_dir,
    _has_research_target,
    _is_scaffold_only,
    _materialize_project,
    _read_prompt_file,
    _resolve_input,
    _slugify,
)
from factory.cli._task_builder import _build_ceo_task
from factory.cli.run import _chain_modes

log = structlog.get_logger()


# ── flag validation ───────────────────────────────────────────


def _validate_ceo_flags(
    args: argparse.Namespace,
) -> tuple[str, bool, bool, bool, str | None, str | None, str | None, str | None] | int:
    """Validate and resolve top-level CLI flags. Returns parsed values or an error code."""
    mode: str = getattr(args, "mode", "auto")
    if mode == "interactive":
        mode = "design"
    bg: bool = getattr(args, "bg", False)
    bg_agents = _resolve_bg_agents(args)
    if bg and bg_agents:
        print("Error: --bg and --bg-agents are mutually exclusive.", file=sys.stderr)
        return 1
    headless: bool = getattr(args, "headless", False) or bg
    prompt_file: str | None = getattr(args, "prompt", None)
    focus: str | None = getattr(args, "focus", None)
    dir_name: str | None = getattr(args, "dir", None)

    raw_path = getattr(args, "path", None)
    if not raw_path:
        print("Error: provide a project path, GitHub URL, idea file, or prompt",
              file=sys.stderr)
        return 1

    no_github = getattr(args, "no_github", False)
    if no_github:
        os.environ["FACTORY_NO_GITHUB"] = "1"
    refine_request: str | None = getattr(args, "refine", None)

    if refine_request:
        if mode and mode != "auto":
            print(f"Error: --refine and --mode {mode} are mutually exclusive.",
                  file=sys.stderr)
            return 1
        if prompt_file:
            print("Error: --refine and --prompt are mutually exclusive.",
                  file=sys.stderr)
            return 1
        if focus:
            print("Error: --refine and --focus are mutually exclusive.",
                  file=sys.stderr)
            return 1
        if not Path(raw_path).expanduser().resolve().is_dir():
            print("Error: --refine requires an existing project directory, not a URL or idea.",
                  file=sys.stderr)
            return 1

    _design_is_existing = (
        mode == "design"
        and raw_path
        and _safe_is_dir(Path(raw_path).expanduser().resolve())
    )

    if mode == "design":
        if headless:
            flag = "--bg" if bg else "--headless"
            print(f"Error: --mode design requires foreground mode "
                  f"(incompatible with {flag})", file=sys.stderr)
            return 1
        if prompt_file:
            print("Error: --mode design and --prompt are mutually exclusive. "
                  "Design mode generates the spec; --prompt provides one.",
                  file=sys.stderr)
            return 1
        if focus and not _design_is_existing:
            print("Error: --mode design and --focus are mutually exclusive "
                  "for new ideas. To discuss a topic on an existing project, "
                  "pass the project path: factory ceo /path --mode design --focus \"topic\"",
                  file=sys.stderr)
            return 1

    if mode == "create":
        if headless:
            flag = "--bg" if bg else "--headless"
            print(f"Error: --mode create requires foreground mode "
                  f"(incompatible with {flag})", file=sys.stderr)
            return 1
        if prompt_file:
            print("Error: --mode create and --prompt are mutually exclusive. "
                  "Create mode generates the workflow from a description.",
                  file=sys.stderr)
            return 1

    if mode == "research" and prompt_file:
        print("Error: --mode research and --prompt are mutually exclusive. "
              "Research ideation generates the spec; --prompt provides one.",
              file=sys.stderr)
        return 1

    return (mode, headless, bg, bg_agents, prompt_file, focus, dir_name, refine_request)


# ── project resolution ────────────────────────────────────────


def _resolve_ceo_project(
    raw_path: str,
    mode: str,
    headless: bool,
    bg: bool,
    focus: str | None,
    dir_name: str | None,
    prompt_file: str | None,
) -> (
    tuple[Path, str | None, str | None, str | None, str | None, bool, bool, str | None]
    | int
):
    """Resolve the project path and mode-specific context.

    Returns (project_path, context, design_idea, research_ideation, deferred_spec,
             needs_materialize, design_existing, create_description) or error code.
    """
    create_description: str | None = None
    design_idea: str | None = None
    design_existing: bool = False
    research_ideation: str | None = None
    deferred_spec: str | None = None
    needs_materialize = False
    context: str | None = None

    _design_is_existing = (
        mode == "design"
        and raw_path
        and _safe_is_dir(Path(raw_path).expanduser().resolve())
    )

    if mode == "create":
        resolved_path = Path(raw_path).expanduser().resolve()
        if not _safe_is_dir(resolved_path):
            print("Error: --mode create requires an existing project directory. "
                  "Pass the factory project path: factory ceo /path/to/factory --mode create",
                  file=sys.stderr)
            return 1
        project_path, context = _resolve_input(raw_path, dir_name=dir_name)
        create_description = focus if focus else context
    elif mode == "design" and _design_is_existing:
        project_path, context = _resolve_input(raw_path, dir_name=dir_name)
        design_existing = True
    elif mode == "design":
        resolved_file = Path(raw_path).expanduser()
        if resolved_file.is_file():
            design_idea = resolved_file.read_text()
            slug = _slugify(dir_name) if dir_name else _slugify(resolved_file.stem.split("—")[0].strip())
            project_path = _dedupe_project_path(_get_projects_dir() / slug, design_idea)
            deferred_spec = design_idea
            needs_materialize = True
            print(f"Idea file: {resolved_file.name}")
            print(f"Project directory: {project_path}")
        else:
            design_idea = raw_path
            slug = _slugify(dir_name) if dir_name else _extract_project_name(raw_path)
            project_path = _dedupe_project_path(_get_projects_dir() / slug, raw_path)
            deferred_spec = raw_path
            needs_materialize = True
        context = None
    elif (
        mode == "research"
        and not _safe_is_dir(resolved := Path(raw_path).expanduser())
        and not _safe_is_file(resolved)
    ):
        if headless:
            flag = "--bg" if bg else "--headless"
            print("Error: --mode research for new projects requires foreground mode "
                  f"(incompatible with {flag})", file=sys.stderr)
            return 1
        if focus:
            print("Error: --focus cannot be used with research ideation for new projects. "
                  "--focus targets existing backlog items.", file=sys.stderr)
            return 1
        research_ideation = raw_path
        slug = _slugify(dir_name) if dir_name else _extract_project_name(raw_path)
        project_path = _dedupe_project_path(_get_projects_dir() / slug, raw_path)
        needs_materialize = True
        context = None
    else:
        project_path, context = _resolve_input(raw_path, dir_name=dir_name)
        if context is not None and not (project_path / ".git").is_dir():
            deferred_spec = context
            needs_materialize = True

    if prompt_file:
        context = _read_prompt_file(project_path, prompt_file)

    return (project_path, context, design_idea, research_ideation,
            deferred_spec, needs_materialize, design_existing, create_description)


# ── late validation ───────────────────────────────────────────


def _validate_late_flags(
    mode: str,
    focus: str | None,
    prompt_file: str | None,
    research_ideation: str | None,
    design_existing: bool,
    project_path: Path,
    no_github: bool,
    issue_number: int | None,
) -> int | None:
    """Run validations that depend on resolved project state. Returns error code or None."""
    if mode == "research" and not research_ideation and not _has_research_target(project_path):
        print("Error: --mode research requires research_target in factory.md. "
              "Either configure research_target manually, or pass an idea string "
              "to start research ideation: factory ceo \"your idea\" --mode research",
              file=sys.stderr)
        return 1

    if focus and prompt_file:
        print("Error: --focus (targeted mode) and --prompt are mutually exclusive. "
              "--focus builds one backlog item; --prompt executes a spec file.", file=sys.stderr)
        return 1

    if focus and mode not in ("improve", "research", "create") and not design_existing:
        print(f"Error: --focus (targeted mode) only works in improve, research, or create mode, "
              f"got '{mode}'. The project must already be built before targeting specific items.",
              file=sys.stderr)
        return 1

    return None


# ── execution ─────────────────────────────────────────────────


def _execute_ceo(
    *,
    args: argparse.Namespace,
    project_path: Path,
    context: str | None,
    mode: str,
    banner_mode: str,
    headless: bool,
    bg: bool,
    bg_agents: bool,
    focus: str | None,
    prompt_file: str | None,
    design_idea: str | None,
    design_existing: bool,
    research_ideation: str | None,
    create_description: str | None,
    deferred_spec: str | None,
    needs_materialize: bool,
    refine_request: str | None,
    issue_number: int | None,
    issue_url: str | None,
    no_github: bool,
    raw_path: str,
) -> int:
    """Set up worktree, build task, and run the CEO agent."""
    from factory.agents.runner import begin_cycle_session, complete_cycle_session, resolve_prompt
    from factory.runners import get_runner
    from factory.runners.claude import _make_ceo_message_emitter
    from factory.worktree import create_worktree, prune_stale, remove_worktree

    discover_only = getattr(args, "discover_only", False)
    min_growth = getattr(args, "min_growth", None)
    max_new = getattr(args, "max_new", None)
    branch = getattr(args, "branch", None)
    run_id = getattr(args, "run_id", None)
    model = _resolve_model(args)
    runner_name = _resolve_runner(args)
    use_profile = getattr(args, "use_profile", False)
    tmux_persist = _resolve_tmux_persist(args)
    background = _resolve_background(args)
    if bg_agents:
        background = False
    if background and tmux_persist:
        print("Error: --bg and --tmux-persist are mutually exclusive.", file=sys.stderr)
        return 1
    clean_pr_flag = getattr(args, "clean_pr", None)

    _print_banner(banner_mode)
    _ensure_dashboard(project_path)

    if needs_materialize:
        _materialize_project(project_path, deferred_spec)

    pruned = prune_stale(project_path)
    if pruned:
        print(f"  Cleaned {len(pruned)} stale worktree(s)", file=sys.stderr)

    if focus:
        from factory.study import add_backlog_item
        add_backlog_item(project_path, focus)

    from factory.messages import mark_read, read_pending
    pending = read_pending(project_path)
    pending_ids = [m.id for m in pending]
    base_branch = branch or _read_target_branch(project_path)
    wt_path, wt_branch = create_worktree(project_path, base_branch, run_id=run_id)

    from factory.skill_cache import ensure_skills
    ensure_skills(wt_path)

    interactive = design_existing or bool(design_idea) or bool(research_ideation) or mode == "create"
    ceo_mode = "create" if mode == "create" else ("build" if interactive else mode)

    if clean_pr_flag is not None:
        clean_pr_resolved = clean_pr_flag
    else:
        config_path = project_path / ".factory" / "config.json"
        if config_path.exists():
            try:
                _cfg = json.loads(config_path.read_text())
                clean_pr_resolved = bool(_cfg.get("clean_pr", False))
            except (json.JSONDecodeError, OSError):
                clean_pr_resolved = False
        else:
            clean_pr_resolved = False

    task = _build_ceo_task(
        wt_path, ceo_mode, context, focus=focus, prompt_file=prompt_file,
        min_growth=min_growth, max_new=max_new, branch=branch,
        discover_only=discover_only, no_github=no_github,
        design_idea=design_idea,
        design_existing=design_existing,
        research_ideation=research_ideation,
        messages=pending,
        issue_number=issue_number,
        issue_url=issue_url,
        refine_request=refine_request,
        clean_pr=clean_pr_resolved,
        display_mode=banner_mode,
        create_description=create_description,
    )

    session_name = _derive_session_name(
        focus=focus,
        design_idea=design_idea,
        research_ideation=research_ideation,
        raw_path=raw_path,
        project_path=project_path,
        mode=banner_mode,
    )

    if bg_agents:
        os.environ["FACTORY_BG"] = "1"

    cycle_span_id = begin_cycle_session(project_path, cycle_id=mode, model=model)
    _ceo_start = time.time()

    ceo_tailer = _start_ceo_tailer(
        wt_path, cycle_span_id, _ceo_start,
        on_line=_make_ceo_message_emitter(wt_path),
        is_headless=headless,
    )

    if headless:
        return _run_headless(
            wt_path=wt_path, project_path=project_path, task=task, mode=mode,
            runner_name=runner_name, model=model, session_name=session_name,
            use_profile=use_profile, tmux_persist=tmux_persist, background=background,
            ceo_tailer=ceo_tailer, cycle_span_id=cycle_span_id,
            pending_ids=pending_ids, focus=focus,
            min_growth=min_growth, max_new=max_new, branch=branch,
            discover_only=discover_only, no_github=no_github,
            needs_materialize=needs_materialize, wt_branch=wt_branch,
        )

    try:
        if pending_ids:
            print(
                f"Consuming {len(pending_ids)} message(s): {', '.join(pending_ids)}",
                file=sys.stderr,
            )
            mark_read(project_path, pending_ids)
        from factory.models import AgentRunRequest as _RunReq
        prompt = resolve_prompt("ceo", wt_path, use_profile=use_profile)
        runner = get_runner(runner_name)
        return runner.interactive_run(_RunReq(
            prompt=prompt, task=task, cwd=wt_path,
            model=model, role="ceo", skip_permissions=True,
            session_name=session_name,
        ))
    finally:
        _stop_ceo_tailer(ceo_tailer)
        complete_cycle_session(project_path, cycle_span_id)
        remove_worktree(project_path, wt_path, wt_branch)
        if needs_materialize and _is_scaffold_only(project_path):
            import shutil
            shutil.rmtree(project_path, ignore_errors=True)


def _run_headless(
    *,
    wt_path: Path,
    project_path: Path,
    task: str,
    mode: str,
    runner_name: str | None,
    model: str | None,
    session_name: str,
    use_profile: bool,
    tmux_persist: bool,
    background: bool,
    ceo_tailer: object,
    cycle_span_id: str | None,
    pending_ids: list[str],
    focus: str | None,
    min_growth: int | None,
    max_new: int | None,
    branch: str | None,
    discover_only: bool,
    no_github: bool,
    needs_materialize: bool,
    wt_branch: str,
) -> int:
    """Run the CEO in headless mode with completion guard."""
    from factory.ceo_completion import run_ceo_with_completion_guard
    from factory.messages import mark_read
    from factory.agents.runner import complete_cycle_session
    from factory.worktree import remove_worktree

    try:
        result, code = _run(run_ceo_with_completion_guard(
            wt_path,
            task,
            mode=mode,
            runner_name=runner_name,
            model=model,
            timeout=7200.0,
            session_name=session_name,
            use_profile=use_profile,
            tmux_persist=tmux_persist,
            background=background,
        ))
        print(result)
        if code == 0 and pending_ids:
            mark_read(project_path, pending_ids)
        if code != 0:
            return code
        return _chain_modes(
            project_path, focus=focus,
            min_growth=min_growth, max_new=max_new, branch=branch,
            already_improved=mode in ("improve", "meta") or discover_only,
            model=model, no_github=no_github, use_profile=use_profile,
            tmux_persist=tmux_persist,
            background=background,
        )
    finally:
        _stop_ceo_tailer(ceo_tailer)
        complete_cycle_session(project_path, cycle_span_id)
        remove_worktree(project_path, wt_path, wt_branch)
        if needs_materialize and _is_scaffold_only(project_path):
            import shutil
            shutil.rmtree(project_path, ignore_errors=True)
