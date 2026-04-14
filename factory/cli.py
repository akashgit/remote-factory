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


# ── subcommand handlers ────────────────────────────────────────


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
    summary = study_project(project_path)

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


def _is_github_url(path: str) -> bool:
    """Return True if path looks like a GitHub URL."""
    return path.startswith("https://github.com/") or path.startswith("git@github.com:")


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
        # Ensure Vertex AI env vars are set
        "export CLAUDE_CODE_USE_VERTEX=1",
        "export CLOUD_ML_REGION=your-region",
        "export ANTHROPIC_VERTEX_PROJECT_ID=your-gcp-project",
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



def _run_single_cycle(project_path: Path, mode: str) -> int:
    """Execute a single factory run cycle. Returns 0 on success, 1 on error."""
    skill_path = Path(__file__).resolve().parent.parent / "SKILL.md"
    try:
        skill_content = skill_path.read_text()
    except FileNotFoundError:
        print(f"Error: SKILL.md not found at {skill_path}", file=sys.stderr)
        return 1

    if mode == "discover":
        prompt = (
            "You are the Factory orchestrator. "
            "Run Discover mode: introspect the project, auto-detect eval dimensions, "
            "and generate the eval harness. Do NOT run the Improve loop.\n\n"
            "IMPORTANT: All factory CLI commands must use `uv run python -m factory` "
            "(not bare `python -m factory`) because pydantic is not in the system Python.\n\n"
            f"Project path: {project_path}\n\n"
            f"--- SKILL.md ---\n{skill_content}\n--- END SKILL.md ---"
        )
    else:
        prompt = (
            "You are the Factory orchestrator. "
            "Follow the skill instructions below to run the factory loop on the project.\n\n"
            "IMPORTANT: All factory CLI commands must use `uv run python -m factory` "
            "(not bare `python -m factory`) because pydantic is not in the system Python.\n\n"
            f"Project path: {project_path}\n\n"
            f"--- SKILL.md ---\n{skill_content}\n--- END SKILL.md ---"
        )

    try:
        subprocess.run(
            ["claude", "-p", prompt, "--dangerously-skip-permissions"],
            cwd=project_path,
            check=True,
        )
    except FileNotFoundError:
        print("Error: 'claude' CLI not found on PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"Error: claude exited with code {e.returncode}", file=sys.stderr)
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    path = args.path

    # If a GitHub URL is provided, clone into a temp directory
    if _is_github_url(path):
        tmp_dir = tempfile.mkdtemp(prefix="factory-")
        subprocess.run(["git", "clone", path, tmp_dir], check=True)
        print(f"Cloned {path} to {tmp_dir}")
        project_path = Path(tmp_dir).resolve()
    else:
        project_path = Path(path).resolve()

    mode = getattr(args, "mode", "improve")
    loop = getattr(args, "loop", False)

    if not loop:
        return _run_single_cycle(project_path, mode)

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

            _run_single_cycle(project_path, mode)

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


# ── parser construction ────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Remote Factory — domain-agnostic multi-agent software evolution loop",
    )
    sub = parser.add_subparsers(dest="command")

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

    # status
    p = sub.add_parser("status", help="Print project status summary")
    p.add_argument("path", help="Path to the project")


    # digest
    p = sub.add_parser("digest", help="Summarize recent factory activity across projects")
    p.add_argument("--date", default=None, help="Show activity for a specific date (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")

    # archive
    p = sub.add_parser("archive", help="Write experiment notes to Obsidian vault")
    p.add_argument("path", help="Path to the project")

    # vault-init
    p = sub.add_parser("vault-init", help="Create the factory Obsidian vault")

    # run
    p = sub.add_parser("run", help="Cron entry: invoke claude -p with factory skill")
    p.add_argument("path", help="Path to the project or GitHub URL")
    p.add_argument(
        "--mode",
        choices=["discover", "improve"],
        default="improve",
        help="Run mode: discover (auto-detect evals) or improve (default improvement loop)",
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
        choices=["discover", "improve"],
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
        "digest": cmd_digest,
        "archive": cmd_archive,
        "vault-init": cmd_vault_init,
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
