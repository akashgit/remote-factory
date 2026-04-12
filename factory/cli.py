"""CLI entry point — argparse subcommands wrapping library functions."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
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



def cmd_run(args: argparse.Namespace) -> int:
    project_path = Path(args.path).resolve()
    try:
        subprocess.run(
            ["claude", "-p", f"Run the factory skill on {project_path}"],
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


    # run
    p = sub.add_parser("run", help="Cron entry: invoke claude -p with factory skill")
    p.add_argument("path", help="Path to the project")

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
        "run": cmd_run,
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
