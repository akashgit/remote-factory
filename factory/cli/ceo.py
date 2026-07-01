"""CLI ceo commands — thin dispatcher delegating to extracted modules."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import structlog
import sys
import tempfile
import time
from datetime import datetime
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
    _auto_detect_mode,
    _resolve_background,
    _resolve_bg_agents,
    _resolve_model,
    _resolve_tmux_persist,
    handle_qa_mode,
    handle_review_mode,
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
    _resolve_focus_issue,
    _resolve_input,
    _slugify,
)
from factory.cli._task_builder import _build_ceo_task
from factory.cli.run import _chain_modes

log = structlog.get_logger()


# ── subcommand handlers ────────────────────────────────────────


def cmd_ceo(args: argparse.Namespace) -> int:
    """Launch the Factory CEO agent to orchestrate a project."""
    from factory.agents.runner import resolve_prompt
    from factory.runners import get_runner
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    raw_path = getattr(args, "path", None)
    mode = getattr(args, "mode", "auto")
    if mode == "interactive":
        mode = "design"
    bg = getattr(args, "bg", False)
    bg_agents = _resolve_bg_agents(args)
    if bg and bg_agents:
        print("Error: --bg and --bg-agents are mutually exclusive.", file=sys.stderr)
        return 1
    headless = getattr(args, "headless", False) or bg
    prompt_file = getattr(args, "prompt", None)
    focus = getattr(args, "focus", None)
    dir_name = getattr(args, "dir", None)

    if not raw_path:
        print("Error: provide a project path, GitHub URL, idea file, or prompt",
              file=sys.stderr)
        return 1

    no_github = getattr(args, "no_github", False)
    if no_github:
        os.environ["FACTORY_NO_GITHUB"] = "1"
    refine_request = getattr(args, "refine", None)

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

    # ── review/qa mode early exits ────────────────────────────
    if mode == "review":
        return handle_review_mode(args, raw_path, headless)
    if mode == "qa":
        return handle_qa_mode(args, raw_path, headless)

    # ── mode-specific validation ──────────────────────────────
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
    if mode == "research":
        if prompt_file:
            print("Error: --mode research and --prompt are mutually exclusive. "
                  "Research ideation generates the spec; --prompt provides one.",
                  file=sys.stderr)
            return 1

    # ── resolve project path ──────────────────────────────────
    create_description: str | None = None
    design_idea: str | None = None
    design_existing: bool = False
    research_ideation: str | None = None
    deferred_spec: str | None = None
    needs_materialize = False
    context: str | None = None
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
    elif mode == "research" and not _safe_is_dir(resolved := Path(raw_path).expanduser()) and not _safe_is_file(resolved):
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

    # ── resolve focus/issue ───────────────────────────────────
    issue_number: int | None = None
    issue_url: str | None = None
    if focus:
        from factory.issue import is_issue_ref
        if is_issue_ref(focus) and no_github:
            print("Error: --focus resolved to an issue reference, but --no-github is set. "
                  "Issue fetching requires GitHub/GitLab CLI access.", file=sys.stderr)
            return 1
        issue_resolved = _resolve_focus_issue(focus, project_path)
        if issue_resolved:
            title, context, issue_number, issue_url = issue_resolved
            focus = f"{title} (issue #{issue_number})"

    # ── auto-detect mode ──────────────────────────────────────
    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path, has_prompt=bool(prompt_file or context),
            force_fresh=force_fresh,
        )

    # ── resolve remaining flags ───────────────────────────────
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

    # ── final validation ──────────────────────────────────────
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
        print(f"Error: --focus (targeted mode) only works in improve, research, or create mode, got '{mode}'. "
              "The project must already be built before targeting specific items.", file=sys.stderr)
        return 1

    # ── banner + setup ────────────────────────────────────────
    if design_existing:
        banner_mode = "design"
    elif mode in ("design", "research") and (design_idea or research_ideation):
        banner_mode = "ideation"
    else:
        banner_mode = mode
    _print_banner(banner_mode)
    _ensure_dashboard(project_path)

    if needs_materialize:
        _materialize_project(project_path, deferred_spec)

    from factory.worktree import create_worktree, prune_stale, remove_worktree
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

    from factory.agents.runner import begin_cycle_session, complete_cycle_session
    cycle_span_id = begin_cycle_session(project_path, cycle_id=mode, model=model)

    _ceo_start = time.time()

    from factory.runners.claude import _make_ceo_message_emitter

    ceo_tailer = _start_ceo_tailer(
        wt_path, cycle_span_id, _ceo_start,
        on_line=_make_ceo_message_emitter(wt_path),
        is_headless=headless,
    )

    if headless:
        from factory.ceo_completion import run_ceo_with_completion_guard

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
            if code == 0:
                if pending_ids:
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


# ── tmux integration ──────────────────────────────────────────


_TMUX_SESSION_PREFIX = "factory-"


_TMUX_SESSIONS_FILE = Path("~/.factory/tmux_sessions.json").expanduser()


def _tmux_session_name(project_path: Path) -> str:
    """Derive a tmux session name from a project path."""
    path_hash = hashlib.sha1(str(project_path).encode()).hexdigest()[:6]
    return f"{_TMUX_SESSION_PREFIX}{project_path.name}-{path_hash}"


def _load_tmux_session_mapping() -> dict[str, str]:
    """Load the session->project mapping from ~/.factory/tmux_sessions.json."""
    if _TMUX_SESSIONS_FILE.exists():
        try:
            return json.loads(_TMUX_SESSIONS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_tmux_session_mapping(session: str, project_path: str) -> None:
    """Save a session->project mapping entry to ~/.factory/tmux_sessions.json."""
    mapping = _load_tmux_session_mapping()
    mapping[session] = project_path
    _TMUX_SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TMUX_SESSIONS_FILE.write_text(json.dumps(mapping, indent=2))


def _tmux_available() -> bool:
    """Check if tmux is installed."""
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _tmux_session_alive(session: str) -> bool:
    """Check if a tmux session exists and is alive."""
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    ).returncode == 0


def _build_tmux_run_args(args: argparse.Namespace, project_path: Path, model: str | None) -> str:
    """Build the 'factory ceo ...' command string from parsed args."""
    parts = [f"factory ceo {project_path}"]
    if args.mode:
        parts.append(f"--mode {args.mode}")
    if model:
        parts.append(f"--model {shlex.quote(model)}")
    if getattr(args, "no_github", False):
        parts.append("--no-github")
    if getattr(args, "profile", None):
        parts.append(f"--profile {shlex.quote(args.profile)}")
    if getattr(args, "focus", None):
        parts.append(f"--focus {shlex.quote(args.focus)}")
    if getattr(args, "refine", None):
        parts.append(f"--refine {shlex.quote(args.refine)}")
    if getattr(args, "clean_pr", None) is True:
        parts.append("--clean-pr")
    elif getattr(args, "clean_pr", None) is False:
        parts.append("--no-clean-pr")
    if getattr(args, "runner", None):
        parts.append(f"--runner {shlex.quote(args.runner)}")
    if getattr(args, "prompt", None):
        parts.append(f"--prompt {shlex.quote(args.prompt)}")
    if getattr(args, "branch", None):
        parts.append(f"--branch {shlex.quote(args.branch)}")
    if getattr(args, "min_growth", None) is not None:
        parts.append(f"--min-growth {args.min_growth}")
    if getattr(args, "max_new", None) is not None:
        parts.append(f"--max-new {args.max_new}")
    if getattr(args, "discover_only", False):
        parts.append("--discover-only")
    if getattr(args, "bg_agents", False):
        parts.append("--bg-agents")
    if getattr(args, "tmux_persist", False):
        parts.append("--tmux-persist")
    if getattr(args, "use_profile", False):
        parts.append("--use-profile")
    return " ".join(parts)


def cmd_tmux(args: argparse.Namespace) -> int:
    """Launch factory run inside a detached tmux session."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    project_path = Path(args.path).resolve()
    session = args.session or _tmux_session_name(project_path)

    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    if check.returncode == 0:
        if args.attach:
            print(f"Attaching to existing session: {session}")
            os.execvp("tmux", ["tmux", "attach-session", "-t", session])
        print(f"Session '{session}' already running. Use --attach or:")
        print(f"  tmux attach -t {session}")
        return 0

    _ENV_PREFIXES = ("FACTORY_", "ANTHROPIC_", "BOBSHELL_", "OPENAI_", "CODEX_", "CLAUDE_CODE_", "CLOUD_ML_")
    run_cmd_parts = []
    for key, val in sorted(os.environ.items()):
        if key.startswith(_ENV_PREFIXES):
            run_cmd_parts.append(f"export {key}={shlex.quote(val)}")
    run_cmd_parts.append(f"export PATH={shlex.quote(os.environ.get('PATH', '/usr/bin'))}")

    model = _resolve_model(args)
    run_args = _build_tmux_run_args(args, project_path, model)
    run_cmd_parts.append(run_args)
    shell_cmd = " && ".join(run_cmd_parts)

    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50", shell_cmd],
    )
    if result.returncode != 0:
        print(f"Error: failed to create tmux session '{session}'", file=sys.stderr)
        return 1

    _save_tmux_session_mapping(session, str(project_path))

    time.sleep(3)

    if not _tmux_session_alive(session):
        print(f"Error: session '{session}' exited immediately after launch", file=sys.stderr)
        return 1

    capture = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p"],
        capture_output=True,
        text=True,
    )
    if capture.returncode == 0:
        pane_text = capture.stdout
        _error_markers = ("Error:", "exited", "no server")
        if any(marker in pane_text for marker in _error_markers):
            log.warning("tmux_post_dispatch_warning", session=session)
            print(f"Warning: session '{session}' may have errors:", file=sys.stderr)
            for line in pane_text.strip().splitlines()[-10:]:
                print(f"  {line}", file=sys.stderr)

    print(f"Factory launched in tmux session: {session}")
    print(f"  tmux attach -t {session}    # attach")
    print(f"  tmux kill-session -t {session}  # stop")

    if args.attach:
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])

    return 0


def cmd_tmux_ls(args: argparse.Namespace) -> int:
    """List running factory tmux sessions."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_created}\t#{session_windows}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("No tmux sessions running.")
        return 0

    mapping = _load_tmux_session_mapping()
    factory_sessions = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        name = parts[0]
        if name.startswith(_TMUX_SESSION_PREFIX):
            created = datetime.fromtimestamp(int(parts[1])).strftime("%Y-%m-%d %H:%M") if len(parts) > 1 else "?"
            project = mapping.get(name, "?")
            factory_sessions.append({"session": name, "started": created, "project": project})

    if not factory_sessions:
        if getattr(args, "json_output", False):
            print("[]")
        else:
            print("No factory sessions running.")
        return 0

    if getattr(args, "json_output", False):
        print(json.dumps(factory_sessions, indent=2))
    else:
        print(f"{'Session':<35} {'Started':<20} {'Project'}")
        print("-" * 80)
        for s in factory_sessions:
            print(f"{s['session']:<35} {s['started']:<20} {s['project']}")
    return 0


def cmd_tmux_capture(args: argparse.Namespace) -> int:
    """Capture recent output from a factory tmux session."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    session = getattr(args, "session", None)
    if not session and getattr(args, "path", None):
        project_path = Path(args.path).resolve()
        mapping = _load_tmux_session_mapping()
        for s, p in mapping.items():
            if Path(p).resolve() == project_path:
                session = s
                break
        if not session:
            session = _tmux_session_name(project_path)

    if not session:
        print("Error: specify --session or path to identify the session", file=sys.stderr)
        return 1

    if not _tmux_session_alive(session):
        print(f"Error: session '{session}' not found", file=sys.stderr)
        return 1

    lines = getattr(args, "lines", -100)
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", str(lines)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: failed to capture pane for '{session}'", file=sys.stderr)
        return 1

    print(result.stdout, end="")
    return 0


def cmd_tmux_stop(args: argparse.Namespace) -> int:
    """Stop a factory tmux session."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    if args.session:
        session = args.session
    elif args.path:
        session = _tmux_session_name(Path(args.path).resolve())
    elif getattr(args, "stop_all", False):
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("No tmux sessions running.")
            return 0

        killed = 0
        for name in result.stdout.strip().splitlines():
            if name.startswith(_TMUX_SESSION_PREFIX):
                subprocess.run(["tmux", "kill-session", "-t", name])
                print(f"Stopped: {name}")
                killed += 1

        if killed == 0:
            print("No factory sessions running.")
        else:
            print(f"Stopped {killed} session(s).")
        return 0
    else:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
        )
        sessions = []
        if result.returncode == 0:
            for name in result.stdout.strip().splitlines():
                if name.startswith(_TMUX_SESSION_PREFIX):
                    sessions.append(name)
        if sessions:
            print("Factory sessions that would be stopped:")
            for s in sessions:
                print(f"  {s}")
        else:
            print("No factory sessions running.")
        print("\nUse --all to stop all factory sessions.")
        return 1

    # Kill specific session
    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    if check.returncode != 0:
        print(f"Session '{session}' not found.")
        return 1

    mapping = _load_tmux_session_mapping()
    if session not in mapping and not getattr(args, "force", False):
        print(
            f"Warning: session '{session}' is not in the factory session registry.",
            file=sys.stderr,
        )
        print("It may not be a factory-managed session. Use --force to kill it anyway.", file=sys.stderr)
        return 1

    subprocess.run(["tmux", "kill-session", "-t", session])
    print(f"Stopped: {session}")
    return 0


def cmd_refactory(args: argparse.Namespace) -> int:
    """Launch the re:factory persistent supervisor agent."""
    import shutil

    from factory.agents.runner import resolve_prompt
    from factory.refactory import get_session_id, setup_workspace

    claude_path = shutil.which("claude")
    if not claude_path:
        print("Error: 'claude' CLI not found. Install Claude Code first.", file=sys.stderr)
        return 1

    project_path = Path(getattr(args, "path", None) or Path.cwd()).resolve()

    setup_workspace(project_path)
    reset = getattr(args, "reset", False)
    session_file = project_path / ".refactory" / "session.json"
    is_new_session = reset or not session_file.exists()
    session_id = get_session_id(project_path, reset=reset)
    model = getattr(args, "model", None)

    prompt = resolve_prompt("refactory")
    prompt_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="refactory-prompt-", delete=False,
    )
    prompt_tmp.write(prompt)
    prompt_tmp.close()

    if is_new_session:
        cmd = [
            "claude",
            "--session-id", session_id,
            "--append-system-prompt-file", prompt_tmp.name,
            "--dangerously-skip-permissions",
        ]
    else:
        cmd = [
            "claude",
            "--resume", session_id,
            "--append-system-prompt-file", prompt_tmp.name,
            "--dangerously-skip-permissions",
        ]

    if model:
        cmd.extend(["--model", model])

    os.chdir(project_path)
    os.execvp("claude", cmd)
    return 0  # unreachable after execvp
