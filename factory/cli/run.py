"""Factory run command — single-shot and heartbeat loop execution."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import structlog

from factory.cli._helpers import (
    _emit_cli_event,
    _ensure_dashboard,
    _print_banner,
    _read_target_branch,
    _run,
    warn_deprecated_mode,
)
from factory.cli._mode_handlers import (
    _auto_detect_mode,
    _resolve_background,
    _resolve_bg_agents,
    _resolve_model,
    _resolve_tmux_persist,
)
from factory.cli._path_resolver import (
    _materialize_project,
    _read_prompt_file,
    _resolve_focus_issues,
    _resolve_input,
)
from factory.cli._task_builder import _build_ceo_task

log = structlog.get_logger()


def _resolve_clean_pr(args: argparse.Namespace, project_path: Path) -> bool:
    """Resolve clean_pr flag from CLI args or project config."""
    clean_pr_flag = getattr(args, "clean_pr", None)
    if clean_pr_flag is not None:
        return clean_pr_flag
    config_path = project_path / ".factory" / "config.json"
    if config_path.exists():
        try:
            _cfg = json.loads(config_path.read_text())
            return bool(_cfg.get("clean_pr", False))
        except (json.JSONDecodeError, OSError):
            return False
    return False


def _run_single_cycle(
    project_path: Path,
    mode: str,
    context: str | None = None,
    focus: str | None = None,
    prompt_file: str | None = None,
    min_growth: int | None = None,
    max_new: int | None = None,
    branch: str | None = None,
    discover_only: bool = False,
    no_github: bool = False,
    model: str | None = None,
    issue_number: int | None = None,
    issue_url: str | None = None,
    issue_numbers: list[int] | None = None,
    issue_urls: list[str] | None = None,
    use_profile: bool = False,
    clean_pr: bool = False,
    tmux_persist: bool = False,
    background: bool = False,
    run_id: str | None = None,
    no_worktree: bool = False,
    overwrite: str | None = None,
    engine: str = "langgraph",
    auto_approve: bool = False,
) -> int:
    """Execute one cycle through LangGraph or the explicit legacy skill path."""
    from factory.agents.runner import invoke_agent
    from factory.worktree import create_worktree, remove_worktree

    if focus:
        from factory.study import add_backlog_item

        add_backlog_item(project_path, focus)

    from factory.messages import mark_read, read_pending

    pending = read_pending(project_path)
    pending_ids = [m.id for m in pending]

    base_branch = branch or _read_target_branch(project_path)
    if no_worktree:
        wt_path = project_path
        wt_branch = None
    else:
        wt_path, wt_branch = create_worktree(project_path, base_branch, run_id=run_id)

    if engine == "skill":
        from factory.skill_cache import ensure_skills

        ensure_skills(wt_path)

    workflow_override = None
    if overwrite and mode and mode != "auto":
        from factory.workflow.definitions import register_all
        from factory.workflow.overwrite import apply_overwrite, generate_session_skill

        workflows = register_all()
        if mode in workflows:
            workflow_override = apply_overwrite(workflows[mode], overwrite, wt_path)
            if engine == "skill":
                generate_session_skill(workflow_override, mode, wt_path)

    preserve_langgraph_worktree = engine == "langgraph"
    try:
        if engine == "langgraph":
            from factory.workflow.executor import WorkflowExecutor
            from factory.workflow.primitives import DEFAULT_AGENT_POOL
            from factory.workflow.registry import WorkflowRegistry

            workflow = workflow_override or WorkflowRegistry.get_workflow(mode, wt_path)
            if not workflow:
                print(f'Error: workflow "{mode}" not found', file=sys.stderr)
                return 1

            executor = WorkflowExecutor(
                workflow,
                wt_path,
                agent_pool=DEFAULT_AGENT_POOL,
                auto_approve=auto_approve,
            )
            exec_result = asyncio.run(executor.execute())
            preserve_langgraph_worktree = exec_result.interrupted or not exec_result.completed
            print(
                json.dumps(
                    {
                        "workflow": mode,
                        "engine": "langgraph",
                        "project_path": str(wt_path),
                        "thread_id": exec_result.thread_id,
                        "success": exec_result.success,
                        "completed": exec_result.completed,
                        "interrupted": exec_result.interrupted,
                        "interrupts": exec_result.interrupts,
                        "nodes_executed": exec_result.nodes_executed,
                        "duration_ms": round(exec_result.duration_ms, 1),
                    },
                    indent=2,
                )
            )
            if exec_result.interrupted:
                print(
                    "Resume: "
                    f"factory workflow resume {mode} {wt_path} "
                    f"--thread-id {exec_result.thread_id}",
                    file=sys.stderr,
                )
                return 2
            return 0 if exec_result.success else 1

        task = _build_ceo_task(
            wt_path,
            mode,
            context,
            focus=focus,
            prompt_file=prompt_file,
            min_growth=min_growth,
            max_new=max_new,
            branch=base_branch,
            discover_only=discover_only,
            no_github=no_github,
            messages=pending,
            issue_number=issue_number,
            issue_url=issue_url,
            issue_numbers=issue_numbers,
            issue_urls=issue_urls,
            clean_pr=clean_pr,
        )

        result, code = _run(
            invoke_agent(
                "ceo",
                task,
                wt_path,
                timeout=7200.0,
                dangerously_skip_permissions=True,
                model=model,
                use_profile=use_profile,
                tmux_persist=tmux_persist,
                background=background,
                workflow_mode=mode,
            )
        )

        if code == 0:
            if pending_ids:
                mark_read(project_path, pending_ids)

        print(result)
        return code
    finally:
        if not no_worktree and not preserve_langgraph_worktree:
            assert wt_branch is not None
            remove_worktree(project_path, wt_path, wt_branch)


def _chain_modes(
    project_path: Path,
    focus: str | None = None,
    min_growth: int | None = None,
    max_new: int | None = None,
    branch: str | None = None,
    already_improved: bool = False,
    max_chains: int = 3,
    model: str | None = None,
    no_github: bool = False,
    use_profile: bool = False,
    tmux_persist: bool = False,
    background: bool = False,
    completed_mode: str | None = None,
    no_worktree: bool = False,
    engine: str = "langgraph",
) -> int:
    """After a cycle completes, re-detect state and chain into the next mode.

    This ensures builds and discoveries flow through the full pipeline
    automatically — Build → Discover → Review → Improve — without manual
    re-invocation. Returns 0 when one Improve cycle completes (or all
    chains are exhausted).

    If *completed_mode* names a terminal workflow, returns 0 immediately
    without chaining.
    """
    from factory.models import ProjectState
    from factory.state import detect_state

    if completed_mode:
        from factory.workflow.registry import WorkflowRegistry

        wf = WorkflowRegistry.get_workflow(completed_mode, project_path)
        if wf and wf.terminal:
            print(
                f"[factory] Terminal mode completed: {completed_mode} "
                "— skipping post-completion chaining",
                file=sys.stderr,
            )
            return 0

    for i in range(max_chains):
        state = detect_state(project_path)
        if state == ProjectState.HAS_FACTORY and already_improved:
            return 0
        next_mode = _auto_detect_mode(project_path)
        if next_mode == "improve":
            already_improved = True
        print(
            f"[factory] Chaining: state={state.value} → mode={next_mode} "
            f"(chain {i + 1}/{max_chains})",
            file=sys.stderr,
        )
        code = _run_single_cycle(
            project_path,
            next_mode,
            focus=focus,
            min_growth=min_growth,
            max_new=max_new,
            branch=branch,
            no_github=no_github,
            model=model,
            use_profile=use_profile,
            tmux_persist=tmux_persist,
            background=background,
            no_worktree=no_worktree,
            engine=engine,
        )
        if code != 0:
            return code
    return 0


def _run_heartbeat_loop(
    project_path: Path,
    mode: str,
    context: str | None,
    focus: str | None,
    prompt_file: str | None,
    discover_only: bool,
    no_github: bool,
    model: str | None,
    issue_number: int | None,
    issue_url: str | None,
    issue_numbers: list[int] | None,
    issue_urls: list[str] | None,
    use_profile_flag: bool,
    clean_pr_resolved: bool,
    tmux_persist: bool,
    background: bool,
    run_id: str | None,
    budget_kwargs: dict,
    skip_improve: bool,
    interval: int,
    max_cycles: int | None,
    no_worktree: bool = False,
    completed_mode: str | None = None,
    engine: str = "langgraph",
    auto_approve: bool = False,
) -> int:
    """Continuous heartbeat loop with signal handling."""
    shutdown_event = threading.Event()

    def _shutdown_handler(signum: int, frame: object) -> None:
        shutdown_event.set()

    old_sigterm = signal.signal(signal.SIGTERM, _shutdown_handler)
    old_sigint = signal.signal(signal.SIGINT, _shutdown_handler)

    cycle = 0
    exit_code = 0
    start_time = time.monotonic()

    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[factory] Cycle {cycle} started at {ts}")
            _emit_cli_event(project_path, "cycle.started", {"cycle": cycle, "mode": mode})

            exit_code = _run_single_cycle(
                project_path,
                mode,
                context,
                focus=focus,
                prompt_file=prompt_file,
                discover_only=discover_only,
                no_github=no_github,
                model=model,
                issue_number=issue_number,
                issue_url=issue_url,
                issue_numbers=issue_numbers,
                issue_urls=issue_urls,
                use_profile=use_profile_flag,
                clean_pr=clean_pr_resolved,
                tmux_persist=tmux_persist,
                background=background,
                run_id=run_id,
                no_worktree=no_worktree,
                engine=engine,
                auto_approve=auto_approve,
                **budget_kwargs,
            )
            if exit_code != 0:
                break
            exit_code = _chain_modes(
                project_path,
                focus=focus,
                already_improved=skip_improve,
                min_growth=budget_kwargs.get("min_growth"),
                max_new=budget_kwargs.get("max_new"),
                branch=budget_kwargs.get("branch"),
                model=model,
                no_github=no_github,
                use_profile=use_profile_flag,
                tmux_persist=tmux_persist,
                background=background,
                completed_mode=completed_mode or mode,
                no_worktree=no_worktree,
                engine=engine,
            )
            if exit_code != 0:
                break
            _emit_cli_event(project_path, "cycle.completed", {"cycle": cycle, "mode": mode})

            mode = _auto_detect_mode(project_path, has_prompt=bool(prompt_file or context))

            if shutdown_event.is_set():
                break

            if max_cycles is not None and cycle >= max_cycles:
                break

            print(f"[factory] Cycle {cycle} completed. Sleeping for {interval}s...")

            shutdown_event.wait(interval)

            if shutdown_event.is_set():
                break
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)

    elapsed = time.monotonic() - start_time
    print(
        f"[factory] Shutting down gracefully after {cycle} cycles."
        f" Total runtime: {elapsed:.0f}s"
    )
    return exit_code


def cmd_run(args: argparse.Namespace) -> int:
    """Run factory cycle(s) via the CEO agent. Supports single-shot and heartbeat loop."""
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    project_path, context = _resolve_input(args.path)
    prompt_file = getattr(args, "prompt", None)
    loop = getattr(args, "loop", False)
    focus = getattr(args, "focus", None)
    discover_only = getattr(args, "discover_only", False)
    no_github = getattr(args, "no_github", False)
    if no_github:
        os.environ["FACTORY_NO_GITHUB"] = "1"
    min_growth = getattr(args, "min_growth", None)
    max_new = getattr(args, "max_new", None)
    branch = getattr(args, "branch", None)
    run_id = getattr(args, "run_id", None)
    model = _resolve_model(args)
    use_profile_flag = getattr(args, "use_profile", False)
    tmux_persist = _resolve_tmux_persist(args)
    background = _resolve_background(args)
    bg_agents = _resolve_bg_agents(args)
    if bg_agents:
        background = False
    if background and tmux_persist:
        print("Error: --bg and --tmux-persist are mutually exclusive.", file=sys.stderr)
        return 1
    if background and bg_agents:
        print("Error: --bg and --bg-agents are mutually exclusive.", file=sys.stderr)
        return 1

    if bg_agents:
        os.environ["FACTORY_BG"] = "1"

    if prompt_file:
        context = _read_prompt_file(project_path, prompt_file)
    issue_number: int | None = None
    issue_url: str | None = None
    issue_numbers: list[int] = []
    issue_urls: list[str] = []
    if focus:
        from factory.issue import has_multi_issue_refs

        if has_multi_issue_refs(focus) and no_github:
            print(
                "Error: --focus resolved to an issue reference, but --no-github is set. "
                "Issue fetching requires GitHub/GitLab CLI access.",
                file=sys.stderr,
            )
            return 1
        multi_resolved = _resolve_focus_issues(focus, project_path)
        if multi_resolved:
            if len(multi_resolved) == 1:
                title, context, issue_number, issue_url = multi_resolved[0]
                focus = f"{title} (issue #{issue_number})"
            else:
                parts = []
                for title, ctx, num, url in multi_resolved:
                    parts.append(f"{title} (issue #{num})")
                    issue_numbers.append(num)
                    issue_urls.append(url)
                focus = " + ".join(parts)
                context = None
    mode = getattr(args, "mode", "auto")
    warn_deprecated_mode(mode)
    auto_approve: bool = getattr(args, "auto_approve", False)
    engine: str = getattr(args, "engine", "langgraph")
    if auto_approve and mode != "design":
        print("Error: --auto-approve only applies to --mode design", file=sys.stderr)
        return 1
    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path,
            has_prompt=bool(prompt_file or context),
            force_fresh=force_fresh,
        )

    if focus and loop:
        print(
            "Error: --focus (targeted mode) and --loop are mutually exclusive. "
            "Targeted mode builds exactly one item and exits.",
            file=sys.stderr,
        )
        return 1
    if focus and prompt_file:
        print(
            "Error: --focus (targeted mode) and --prompt are mutually exclusive. "
            "--focus builds one backlog item; --prompt executes a spec file.",
            file=sys.stderr,
        )
        return 1
    if focus and mode not in ("improve", "research"):
        print(
            f"Error: --focus (targeted mode) only works in improve or research mode, got '{mode}'. "
            "The project must already be built before targeting specific items.",
            file=sys.stderr,
        )
        return 1

    no_worktree = getattr(args, "no_worktree", False)
    clean_pr_resolved = _resolve_clean_pr(args, project_path)

    _print_banner(mode)
    _ensure_dashboard(project_path)

    if context is not None and not (project_path / ".git").is_dir():
        _materialize_project(project_path, context)

    from factory.worktree import prune_stale

    if project_path.is_dir():
        pruned = prune_stale(project_path)
        if pruned:
            print(f"  Cleaned {len(pruned)} stale worktree(s)", file=sys.stderr)

    budget_kwargs = dict(min_growth=min_growth, max_new=max_new, branch=branch)
    skip_improve = mode in ("improve", "meta") or discover_only

    overwrite = getattr(args, "overwrite", None)

    if not loop:
        code = _run_single_cycle(
            project_path,
            mode,
            context,
            focus=focus,
            prompt_file=prompt_file,
            discover_only=discover_only,
            no_github=no_github,
            model=model,
            issue_number=issue_number,
            issue_url=issue_url,
            issue_numbers=issue_numbers,
            issue_urls=issue_urls,
            use_profile=use_profile_flag,
            clean_pr=clean_pr_resolved,
            tmux_persist=tmux_persist,
            background=background,
            run_id=run_id,
            no_worktree=no_worktree,
            overwrite=overwrite,
            engine=engine,
            auto_approve=auto_approve,
            **budget_kwargs,
        )
        if code != 0:
            return code
        return _chain_modes(
            project_path,
            focus=focus,
            already_improved=skip_improve,
            min_growth=min_growth,
            max_new=max_new,
            branch=branch,
            model=model,
            no_github=no_github,
            use_profile=use_profile_flag,
            tmux_persist=tmux_persist,
            background=background,
            completed_mode=mode,
            no_worktree=no_worktree,
            engine=engine,
        )

    interval: int = getattr(args, "interval", 1800)
    max_cycles: int | None = getattr(args, "max_cycles", None)
    return _run_heartbeat_loop(
        project_path=project_path,
        mode=mode,
        context=context,
        focus=focus,
        prompt_file=prompt_file,
        discover_only=discover_only,
        no_github=no_github,
        model=model,
        issue_number=issue_number,
        issue_url=issue_url,
        issue_numbers=issue_numbers,
        issue_urls=issue_urls,
        use_profile_flag=use_profile_flag,
        clean_pr_resolved=clean_pr_resolved,
        tmux_persist=tmux_persist,
        background=background,
        run_id=run_id,
        budget_kwargs=budget_kwargs,
        skip_improve=skip_improve,
        interval=interval,
        max_cycles=max_cycles,
        no_worktree=no_worktree,
        completed_mode=mode,
        engine=engine,
        auto_approve=auto_approve,
    )
