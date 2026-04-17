"""CLI entry point — argparse subcommands wrapping library functions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _run(coro):  # noqa: ANN001, ANN202
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ── banner ────────────────────────────────────────────────────


def _print_banner(mode: str = "improve") -> None:
    """Print the Factory startup banner to stderr."""
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        print(f"Factory v2 — mode: {mode}", file=sys.stderr)
        return

    c = "\033[1;36m"  # bold cyan
    d = "\033[2m"      # dim
    r = "\033[0m"      # reset

    banner = (
        f"\n{c}  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻{r}\n"
        f"{c}  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛{r}\n"
        f"{c}  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹ {r}\n"
        f"{d}  Multi-Agent Software Evolution{r}\n"
        f"{d}  Mode: {mode}{r}\n"
    )
    print(banner, file=sys.stderr)


# ── subcommand handlers ────────────────────────────────────────


def cmd_home(args: argparse.Namespace) -> int:
    """Print the factory installation root directory."""
    factory_home = Path(__file__).resolve().parent.parent
    print(factory_home)
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from factory.state import detect_state

    state = detect_state(Path(args.path))
    print(state.value)
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from factory.discovery.generate import write_eval_script
    from factory.discovery.introspect import introspect_project
    from factory.discovery.profile import build_eval_profile
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    profile = introspect_project(project_path)
    eval_profile = build_eval_profile(profile)

    # Persist artifacts so detect_state can find them
    store = ExperimentStore(project_path)
    store.factory_dir.mkdir(exist_ok=True)
    _run(store.save_eval_profile(eval_profile))
    write_eval_script(eval_profile, project_path)

    output = {
        "project": profile.model_dump(),
        "eval_profile": eval_profile.model_dump(),
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)

    factory_md = project_path / "factory.md"
    if not factory_md.exists():
        print("Error: factory.md not found. Create it first or use --reparse.", file=sys.stderr)
        return 1

    # Ensure .factory/ dir exists so reparse_config can write config.json
    store.factory_dir.mkdir(exist_ok=True)
    config = _run(store.reparse_config())

    if args.reparse:
        print(f"Reparsed config: goal={config.goal!r}")
    else:
        _run(store.init(config))
        print(f"Initialized .factory/ — goal={config.goal!r}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from factory.eval.runner import run_eval
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    config = _run(store.read_config())
    score = _run(run_eval(config.eval_command, project_path, config.eval_threshold))
    print(json.dumps(score.model_dump(), indent=2, default=str))
    return 0 if score.passed else 1


def cmd_guard(args: argparse.Namespace) -> int:
    from factory.eval.guards import check_all

    project_path = Path(args.path)

    # Optionally load scope from factory config
    scope = None
    if args.check_scope:
        from factory.store import ExperimentStore
        store = ExperimentStore(project_path)
        config = _run(store.read_config())
        scope = config.scope

    violations = check_all(project_path, args.baseline, allowed_scope=scope)
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("clean")
    return 0


def cmd_begin(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    store = ExperimentStore(Path(args.path))
    exp_id = _run(store.begin(args.hypothesis))
    print(exp_id)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore
    from factory.models import ExperimentRecord

    store = ExperimentStore(Path(args.path))
    record = ExperimentRecord(
        id=args.id,
        timestamp=datetime.now(),
        hypothesis=args.hypothesis or "",
        change_summary=args.summary or "",
        issue_number=args.issue,
        pr_number=args.pr,
        score_before=None,
        score_after=None,
        delta=None,
        verdict=args.verdict,
        cost_usd=args.cost,
        notes=args.notes or "",
    )
    _run(store.finalize(args.id, record))
    print(f"Finalized experiment {args.id} — verdict={args.verdict}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    store = ExperimentStore(Path(args.path))
    records = _run(store.load_history())
    if not records:
        print("No experiments recorded.")
        return 0

    header = f"{'ID':>4}  {'Verdict':>7}  {'Delta':>8}  {'Cost':>8}  Hypothesis"
    print(header)
    print("-" * len(header))
    for r in records:
        delta = f"{r.delta:+.4f}" if r.delta is not None else "    n/a"
        cost = f"${r.cost_usd:.2f}" if r.cost_usd is not None else "     n/a"
        hyp = r.hypothesis[:60]
        print(f"{r.id:>4}  {r.verdict:>7}  {delta:>8}  {cost:>8}  {hyp}")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    from factory.notify.telegram import TelegramNotifier
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    records = _run(store.load_history())
    notifier = TelegramNotifier()
    _run(notifier.send_digest(project_path.name, records, None))
    print("Digest sent.")
    return 0


def cmd_study(args: argparse.Namespace) -> int:
    from factory.study import study_project

    project_path = Path(args.path)
    kwargs: dict[str, object] = {}
    projects_dir = getattr(args, "projects_dir", None)
    if projects_dir:
        kwargs["projects_dir"] = str(Path(projects_dir).expanduser().resolve())
    summary = study_project(project_path, **kwargs)

    # Write to .factory/strategy/observations.md
    obs_path = project_path / ".factory" / "strategy" / "observations.md"
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    obs_path.write_text(summary)

    print(summary)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from factory.state import detect_state
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    state = detect_state(project_path)
    print(f"Project: {project_path}")
    print(f"State: {state.value}")

    if state.value == "has_factory":
        store = ExperimentStore(project_path)
        try:
            config = _run(store.read_config())
        except FileNotFoundError:
            config = None

        # Try to read latest eval score
        profile = _run(store.read_eval_profile())
        if profile:
            dims = ", ".join(d.name for d in profile.dimensions)
            print(f"Eval dimensions: {dims}")

        records = _run(store.load_history())
        if records:
            kept = sum(1 for r in records if r.verdict == "keep")
            reverted = sum(1 for r in records if r.verdict == "revert")
            total = len(records)
            print(f"Experiments: {total} total ({kept} kept, {reverted} reverted)")
            last = records[-1]
            print(f'Last experiment: #{last.id} — "{last.hypothesis}" ({last.verdict})')
            scores = [r.score_after for r in records if r.score_after is not None]
            if scores:
                print(f"Latest score: {scores[-1]:.3f}")
        else:
            print("Experiments: none")

        if config:
            print(f"Goal: {config.goal}")

    return 0



def cmd_ace(args: argparse.Namespace) -> int:
    """Run ACE self-improvement on agent playbooks."""
    from factory.ace.curator import curate_playbook
    from factory.ace.models import Playbook
    from factory.ace.reflector import reflect_on_experiments

    project_path = Path(args.path).resolve()
    projects_dir = Path(args.projects_dir).expanduser().resolve()
    dry_run = getattr(args, "dry_run", False)

    # Step 1: Reflect — analyze experiment data, generate candidate bullets
    candidates = reflect_on_experiments(projects_dir, project_path)

    if not candidates:
        print("No candidate playbook bullets generated (not enough experiment data).")
        return 0

    # Step 2: Curate — merge with existing playbooks, prune
    playbooks_dir = Path(__file__).parent / "agents" / "playbooks"
    playbooks_dir.mkdir(parents=True, exist_ok=True)

    for role, items in candidates.items():
        # Load existing playbook if any
        playbook_path = playbooks_dir / f"{role}.md"
        if playbook_path.exists():
            existing = Playbook.from_markdown(playbook_path.read_text())
        else:
            existing = Playbook.empty(role)

        # Curate: merge candidates, prune, cap
        updated = curate_playbook(existing, items)

        if dry_run:
            print(f"\n{'=' * 60}")
            print(f"DRY RUN — {role} ({len(items)} candidates → {len(updated.items)} items)")
            print(f"{'=' * 60}")
            print(updated.to_markdown())
        else:
            playbook_path.write_text(updated.to_markdown())
            print(f"  {role}: {len(updated.items)} items → {playbook_path}")

    if not dry_run:
        print(f"\nPlaybooks updated in {playbooks_dir}")

    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from factory.digest import format_digest, scan_vault

    target_date = None
    if args.date:
        from datetime import date as date_cls
        target_date = date_cls.fromisoformat(args.date)

    projects = scan_vault(target_date=target_date, days=args.days)
    output = format_digest(projects, target_date=target_date, days=args.days)
    print(output)
    return 0


def cmd_insights(args: argparse.Namespace) -> int:
    from factory.insights import (
        analyze,
        discover_projects,
        format_insights,
        load_all_histories,
    )

    project_path = Path(args.path).resolve()
    projects_dir = Path(args.projects_dir).expanduser().resolve()
    project_paths = discover_projects(projects_dir)

    if not project_paths:
        print("No factory-managed projects found.")
        return 0

    histories = load_all_histories(project_paths)
    if not histories:
        print("No experiment histories found.")
        return 0

    insights = analyze(histories)
    report = format_insights(insights)

    # Write to .factory/strategy/insights.md
    out_path = project_path / ".factory" / "strategy" / "insights.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)

    print(report)
    print(f"\nWritten to {out_path}")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    from factory.obsidian.notes import (
        update_memory_index,
        write_experiment_note,
        write_project_dashboard,
        write_strategy_note,
    )
    from factory.state import detect_state
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    records = _run(store.load_history())

    if not records:
        print("Nothing to archive.")
        return 0

    project_name = project_path.name
    state = detect_state(project_path).value

    # Write experiment notes
    for record in records:
        write_experiment_note(project_name, record)

    # Build eval_dimensions list for dashboard
    eval_dimensions: list[dict] | None = None
    profile = _run(store.read_eval_profile())
    if profile:
        eval_dimensions = [d.model_dump() for d in profile.dimensions]

    # Current score from latest experiment
    scores = [r.score_after for r in records if r.score_after is not None]
    current_score = scores[-1] if scores else None

    write_project_dashboard(project_name, state, current_score, records, eval_dimensions)

    # Write strategy note if strategy exists
    strategy_text = _run(store.read_strategy())
    if strategy_text:
        write_strategy_note(project_name, strategy_text)

    # Update MEMORY.md index
    update_memory_index()

    from factory.obsidian.notes import _get_vault_path

    vault_path = _get_vault_path()
    print(f"Archived {len(records)} experiments to {vault_path}")
    return 0


def cmd_vault_init(args: argparse.Namespace) -> int:
    from factory.obsidian.notes import init_vault

    vault_path = init_vault()
    print(f"Factory vault initialized at {vault_path}")
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    """Invoke a specialist agent with the given task."""
    from factory.agents.runner import invoke_agent

    role = args.role
    task = args.task
    project_path = Path(args.project).resolve()
    timeout = getattr(args, "timeout", 600.0)

    result, code = _run(invoke_agent(
        role,
        task,
        project_path,
        timeout=timeout,
        dangerously_skip_permissions=True,
    ))
    print(result)
    return code


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Launch the Factory live dashboard server."""
    from factory.dashboard.app import create_app

    projects_dir = Path(args.projects_dir).expanduser().resolve()
    port = args.port
    host = args.host

    _print_banner("dashboard")
    print(f"  Dashboard: http://{host}:{port}", file=sys.stderr)
    print(f"  Projects:  {projects_dir}\n", file=sys.stderr)

    app = create_app(projects_dir)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def cmd_ceo(args: argparse.Namespace) -> int:
    """Launch the Factory CEO agent to orchestrate a project."""
    from factory.agents.runner import invoke_agent

    project_path, context = _resolve_input(args.path)
    mode = getattr(args, "mode", "improve")
    _print_banner(mode)

    task = _build_ceo_task(project_path, mode, context)

    result, code = _run(invoke_agent(
        "ceo",
        task,
        project_path,
        timeout=3600.0,  # CEO gets a longer timeout (1 hour)
        dangerously_skip_permissions=True,
    ))
    print(result)
    return code


def _is_github_url(path: str) -> bool:
    """Return True if path looks like a GitHub URL."""
    return path.startswith("https://github.com/") or path.startswith("git@github.com:")


# ── universal input resolver ─────────────────────────────────


_PROJECTS_DIR = Path(os.environ.get("FACTORY_PROJECTS_DIR", str(Path.home() / "cursor-projects")))

_VAULT_IDEAS_DIRS: list[Path] = [
    # Personal Obsidian vault (optional)
    Path.home() / "Library" / "Mobile Documents" / "personal-vault" / "Ideas",
    # Generic Obsidian vault location
    Path.home() / "obsidian-vaults" / "factory" / "Ideas",
]


def _resolve_input(raw: str) -> tuple[Path, str | None]:
    """Resolve any user input to (project_path, optional_context).

    Handles four input types in priority order:
    1. Existing directory → use directly
    2. GitHub URL → clone
    3. Vault idea name → fuzzy match, create repo, return idea as context
    4. Raw prompt → create repo, return prompt as context
    """
    # 1. Existing directory
    expanded = Path(raw).expanduser()
    if expanded.is_dir():
        return expanded.resolve(), None

    # 2. GitHub URL
    if _is_github_url(raw):
        tmp_dir = tempfile.mkdtemp(prefix="factory-")
        subprocess.run(["git", "clone", raw, tmp_dir], check=True)
        print(f"Cloned {raw} → {tmp_dir}")
        return Path(tmp_dir).resolve(), None

    # 3. Vault idea (fuzzy match)
    match = _match_vault_idea(raw)
    if match:
        idea_content = match.read_text()
        # Derive project name from idea title (before the em dash)
        slug = _slugify(match.stem.split("\u2014")[0].strip())
        project_path = _PROJECTS_DIR / slug
        _ensure_repo(project_path)
        _persist_spec(project_path, idea_content)
        print(f"Matched vault idea: {match.stem}")
        print(f"Project directory: {project_path}")
        return project_path, idea_content

    # 4. Raw prompt
    slug = _slugify(raw[:50])
    project_path = _PROJECTS_DIR / slug
    _ensure_repo(project_path)
    _persist_spec(project_path, raw)
    print(f"New project from prompt: {project_path}")
    return project_path, raw


def _match_vault_idea(query: str) -> Path | None:
    """Fuzzy-match a query against vault idea filenames."""
    q = query.lower().strip()
    candidates: list[tuple[int, Path]] = []

    for ideas_dir in _VAULT_IDEAS_DIRS:
        if not ideas_dir.is_dir():
            continue
        for f in ideas_dir.glob("*.md"):
            if f.name == "Ideas.md":
                continue
            stem = f.stem.lower()
            short = stem.split("\u2014")[0].strip()  # part before em dash

            # Exact match on full stem or short name
            if q == stem or q == short:
                return f

            # Substring match
            if q in stem:
                # Score: shorter match = better (more specific)
                candidates.append((len(stem), f))

            # Word-level match: all query words appear in stem
            q_words = q.split()
            if len(q_words) > 1 and all(w in stem for w in q_words):
                candidates.append((len(stem), f))

    if not candidates:
        return None
    # Best match = shortest stem (most specific)
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    import re

    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:50].rstrip("-") or "factory-project"


def _ensure_repo(project_path: Path) -> None:
    """Create directory + git init if needed."""
    project_path.mkdir(parents=True, exist_ok=True)
    if not (project_path / ".git").is_dir():
        subprocess.run(["git", "init"], cwd=project_path, capture_output=True, check=True)


def _persist_spec(project_path: Path, spec: str) -> None:
    """Write the project spec to .factory/strategy/current.md so all agents can read it.

    This ensures sub-agents spawned by the CEO have access to the original
    idea/prompt, not just the CEO's task string.
    """
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    spec_path = strategy_dir / "current.md"
    if not spec_path.exists():
        spec_path.write_text(f"## Project Specification\n\n{spec}\n")


# ── tmux integration ──────────────────────────────────────────


_TMUX_SESSION_PREFIX = "factory-"


def _tmux_session_name(project_path: Path) -> str:
    """Derive a tmux session name from a project path."""
    return f"{_TMUX_SESSION_PREFIX}{project_path.name}"


def _tmux_available() -> bool:
    """Check if tmux is installed."""
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def cmd_tmux(args: argparse.Namespace) -> int:
    """Launch factory run inside a detached tmux session."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    project_path = Path(args.path).resolve()
    session = args.session or _tmux_session_name(project_path)

    # Check if session already exists
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

    # Build the factory run command
    factory_root = Path(__file__).resolve().parent.parent
    run_cmd_parts = [
        f"cd {factory_root}",
        "source .venv/bin/activate",
        # Ensure Vertex AI env vars are set (inherit from current env)
        f"export CLAUDE_CODE_USE_VERTEX={os.environ.get('CLAUDE_CODE_USE_VERTEX', '1')}",
        f"export CLOUD_ML_REGION={os.environ.get('CLOUD_ML_REGION', 'your-region')}",
        f"export ANTHROPIC_VERTEX_PROJECT_ID={os.environ.get('ANTHROPIC_VERTEX_PROJECT_ID', '')}",
        # Ensure gcloud SDK is on PATH
        'export PATH="$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"',
    ]

    run_args = f"uv run python -m factory run {project_path}"
    if args.mode:
        run_args += f" --mode {args.mode}"
    if args.loop:
        run_args += " --loop"
    if args.interval:
        run_args += f" --interval {args.interval}"
    if args.max_cycles is not None:
        run_args += f" --max-cycles {args.max_cycles}"

    run_cmd_parts.append(run_args)
    shell_cmd = " && ".join(run_cmd_parts)

    # Create detached tmux session
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50", shell_cmd],
    )
    if result.returncode != 0:
        print(f"Error: failed to create tmux session '{session}'", file=sys.stderr)
        return 1

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

    factory_sessions = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        name = parts[0]
        if name.startswith(_TMUX_SESSION_PREFIX):
            created = datetime.fromtimestamp(int(parts[1])).strftime("%Y-%m-%d %H:%M") if len(parts) > 1 else "?"
            factory_sessions.append((name, created))

    if not factory_sessions:
        print("No factory sessions running.")
        return 0

    print(f"{'Session':<30} {'Started':<20}")
    print("-" * 50)
    for name, created in factory_sessions:
        print(f"{name:<30} {created:<20}")
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
    else:
        # Stop all factory sessions
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

    # Kill specific session
    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    if check.returncode != 0:
        print(f"Session '{session}' not found.")
        return 1

    subprocess.run(["tmux", "kill-session", "-t", session])
    print(f"Stopped: {session}")
    return 0



def _build_ceo_task(project_path: Path, mode: str, context: str | None = None) -> str:
    """Build the CEO agent task string from mode and optional context."""
    task = f"Project: {project_path}\nMode: {mode}"

    if context:
        task += f"\n\n## Project Specification\n\n{context}"

    if mode == "discover":
        task += (
            "\n\nRun Discover mode: introspect the project, auto-detect eval dimensions, "
            "and generate the eval harness. Do NOT run the Improve loop."
        )
    elif mode == "meta":
        task += (
            "\n\nRun Meta mode: self-improvement only. Collect cross-project data, "
            "run ACE for all agent roles, record playbook evolution, commit."
        )

    return task


def _run_single_cycle(project_path: Path, mode: str, context: str | None = None) -> int:
    """Execute a single factory run cycle via the CEO agent. Returns 0 on success, 1 on error."""
    from factory.agents.runner import invoke_agent

    task = _build_ceo_task(project_path, mode, context)

    result, code = _run(invoke_agent(
        "ceo",
        task,
        project_path,
        timeout=3600.0,
        dangerously_skip_permissions=True,
    ))
    print(result)
    return code


def cmd_run(args: argparse.Namespace) -> int:
    """Run factory cycle(s) via the CEO agent. Supports single-shot and heartbeat loop."""
    project_path, context = _resolve_input(args.path)
    mode = getattr(args, "mode", "improve")
    loop = getattr(args, "loop", False)
    _print_banner(mode)

    if not loop:
        return _run_single_cycle(project_path, mode, context)

    # Heartbeat loop mode
    interval: int = getattr(args, "interval", 1800)
    max_cycles: int | None = getattr(args, "max_cycles", None)
    shutdown_requested = False

    def _shutdown_handler(signum: int, frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True

    old_sigterm = signal.signal(signal.SIGTERM, _shutdown_handler)
    old_sigint = signal.signal(signal.SIGINT, _shutdown_handler)

    cycle = 0
    start_time = time.monotonic()

    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[factory] Cycle {cycle} started at {ts}")
            _emit_cli_event(project_path, "cycle.started", {"cycle": cycle, "mode": mode})

            _run_single_cycle(project_path, mode, context)
            _emit_cli_event(project_path, "cycle.completed", {"cycle": cycle, "mode": mode})

            if shutdown_requested:
                break

            if max_cycles is not None and cycle >= max_cycles:
                break

            print(f"[factory] Cycle {cycle} completed. Sleeping for {interval}s...")

            try:
                time.sleep(interval)
            except (KeyboardInterrupt, SystemExit):
                shutdown_requested = True

            if shutdown_requested:
                break
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)

    elapsed = time.monotonic() - start_time
    print(
        f"[factory] Shutting down gracefully after {cycle} cycles."
        f" Total runtime: {elapsed:.0f}s"
    )
    return 0


def _emit_cli_event(project_path: Path, event_type: str, data: dict) -> None:
    """Emit a factory event, swallowing errors."""
    try:
        from factory.events import emit_event

        emit_event(project_path, event_type, data=data)
    except Exception:
        pass


# ── parser construction ────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Remote Factory — domain-agnostic multi-agent software evolution loop",
    )
    sub = parser.add_subparsers(dest="command")

    # home
    sub.add_parser("home", help="Print factory installation root directory")

    # detect
    p = sub.add_parser("detect", help="Print project state")
    p.add_argument("path", help="Path to the project")

    # discover
    p = sub.add_parser("discover", help="Introspect project and generate eval profile")
    p.add_argument("path", help="Path to the project")

    # init
    p = sub.add_parser("init", help="Create .factory/ or reparse factory.md")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--reparse", action="store_true", help="Reparse existing factory.md")

    # eval
    p = sub.add_parser("eval", help="Run project evals, print JSON CompositeScore")
    p.add_argument("path", help="Path to the project")

    # guard
    p = sub.add_parser("guard", help="Check guard rules, print violations or 'clean'")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--baseline", required=True, help="Baseline commit SHA")
    p.add_argument("--check-scope", action="store_true", help="Also check file scope")

    # begin
    p = sub.add_parser("begin", help="Start experiment, print ID")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--hypothesis", required=True, help="Experiment hypothesis text")

    # finalize
    p = sub.add_parser("finalize", help="Finalize experiment with verdict")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--id", required=True, type=int, help="Experiment ID")
    p.add_argument("--verdict", required=True, choices=["keep", "revert", "error"],
                    help="Experiment verdict")
    p.add_argument("--hypothesis", default=None, help="Hypothesis text")
    p.add_argument("--summary", default=None, help="Change summary")
    p.add_argument("--cost", default=None, type=float, help="Cost in USD")
    p.add_argument("--issue", default=None, type=int, help="GitHub issue number")
    p.add_argument("--pr", default=None, type=int, help="GitHub PR number")
    p.add_argument("--notes", default=None, help="Additional notes")

    # history
    p = sub.add_parser("history", help="Print formatted experiment history table")
    p.add_argument("path", help="Path to the project")

    # notify
    p = sub.add_parser("notify", help="Send Telegram digest")
    p.add_argument("path", help="Path to the project")

    # study
    p = sub.add_parser("study", help="Read interaction logs and write observations")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--projects-dir", default=None,
        help="Directory containing factory-managed projects for cross-project insights",
    )

    # status
    p = sub.add_parser("status", help="Print project status summary")
    p.add_argument("path", help="Path to the project")


    # insights
    p = sub.add_parser("insights", help="Cross-project analysis of experiment histories")
    p.add_argument("path", help="Path to the project (insights.md written here)")
    p.add_argument(
        "--projects-dir", default="~/cursor-projects",
        help="Directory containing factory-managed projects (default: ~/cursor-projects)",
    )

    # ace
    p = sub.add_parser("ace", help="Run ACE self-improvement on agent playbooks")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--projects-dir", default="~/cursor-projects",
        help="Directory containing factory-managed projects (default: ~/cursor-projects)",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print candidates without writing playbooks",
    )

    # digest
    p = sub.add_parser("digest", help="Summarize recent factory activity across projects")
    p.add_argument("--date", default=None, help="Show activity for a specific date (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")

    # archive
    p = sub.add_parser("archive", help="Write experiment notes to Obsidian vault")
    p.add_argument("path", help="Path to the project")

    # vault-init
    p = sub.add_parser("vault-init", help="Create the factory Obsidian vault")

    # dashboard — live web dashboard
    p = sub.add_parser("dashboard", help="Launch the live Factory dashboard")
    p.add_argument(
        "--projects-dir", default="~/cursor-projects",
        help="Directory containing factory-managed projects (default: ~/cursor-projects)",
    )
    p.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    p.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")

    # agent — invoke a specialist agent directly
    p = sub.add_parser("agent", help="Invoke a specialist agent with a task")
    p.add_argument("role", choices=["researcher", "strategist", "builder", "reviewer",
                                     "evaluator", "archivist", "ceo"],
                    help="Agent role to invoke")
    p.add_argument("--task", required=True, help="Task description for the agent")
    p.add_argument("--project", required=True, help="Path to the project")
    p.add_argument("--timeout", type=float, default=600.0,
                    help="Timeout in seconds (default: 600)")

    # ceo — launch the Factory CEO agent directly
    p = sub.add_parser("ceo", help="Launch the Factory CEO agent to orchestrate a project")
    p.add_argument("path", help="Project path, GitHub URL, vault idea name, or prompt")
    p.add_argument(
        "--mode",
        choices=["discover", "improve", "meta"],
        default="improve",
        help="Run mode: discover, improve (default), or meta (self-improvement only)",
    )

    # run
    p = sub.add_parser("run", help="Run factory cycle (delegates to CEO agent)")
    p.add_argument("path", help="Project path, GitHub URL, vault idea name, or prompt")
    p.add_argument(
        "--mode",
        choices=["discover", "improve", "meta"],
        default="improve",
        help="Run mode: discover, improve (default), or meta (self-improvement only)",
    )
    p.add_argument(
        "--loop", action="store_true", default=False,
        help="Enable heartbeat mode: run continuously with sleep between cycles",
    )
    p.add_argument(
        "--interval", type=int, default=1800,
        help="Seconds to sleep between cycles (default: 1800)",
    )
    p.add_argument(
        "--max-cycles", type=int, default=None,
        help="Maximum number of cycles (default: unlimited)",
    )

    # tmux — launch factory run in a detached tmux session
    p = sub.add_parser("tmux", help="Launch factory run in a detached tmux session")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--session", default=None, help="Custom tmux session name")
    p.add_argument(
        "--mode",
        choices=["discover", "improve", "meta"],
        default="improve",
        help="Run mode (default: improve)",
    )
    p.add_argument("--loop", action="store_true", default=False, help="Enable loop mode")
    p.add_argument("--interval", type=int, default=1800, help="Loop interval in seconds")
    p.add_argument("--max-cycles", type=int, default=None, help="Max cycles for loop mode")
    p.add_argument("--attach", action="store_true", default=False,
                    help="Attach to session after creating")

    # tmux-ls — list factory tmux sessions
    sub.add_parser("tmux-ls", help="List running factory tmux sessions")

    # tmux-stop — stop factory tmux sessions
    p = sub.add_parser("tmux-stop", help="Stop factory tmux session(s)")
    p.add_argument("--session", default=None, help="Session name to stop")
    p.add_argument("--path", default=None, help="Project path (derives session name)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "home": cmd_home,
        "detect": cmd_detect,
        "discover": cmd_discover,
        "init": cmd_init,
        "eval": cmd_eval,
        "guard": cmd_guard,
        "begin": cmd_begin,
        "finalize": cmd_finalize,
        "history": cmd_history,
        "notify": cmd_notify,
        "study": cmd_study,
        "status": cmd_status,
        "insights": cmd_insights,
        "ace": cmd_ace,
        "digest": cmd_digest,
        "archive": cmd_archive,
        "vault-init": cmd_vault_init,
        "dashboard": cmd_dashboard,
        "agent": cmd_agent,
        "ceo": cmd_ceo,
        "run": cmd_run,
        "tmux": cmd_tmux,
        "tmux-ls": cmd_tmux_ls,
        "tmux-stop": cmd_tmux_stop,
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
