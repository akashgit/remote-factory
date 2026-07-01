"""CLI admin commands."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import structlog
import sys
from pathlib import Path

from factory.cli._helpers import _emit_cli_event, _run

log = structlog.get_logger()

def cmd_home(args: argparse.Namespace) -> int:
    """Print the factory package root (where templates/ lives)."""
    factory_home = Path(__file__).resolve().parent.parent
    print(factory_home)
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from factory.state import detect_state

    project_path = Path(args.path)
    state = detect_state(project_path)
    _emit_cli_event(project_path, "detect", {"state": state.value})
    print(state.value)
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from factory.discovery.eval_spec import generate_eval_spec
    from factory.discovery.generate import write_eval_script
    from factory.discovery.introspect import introspect_project
    from factory.discovery.profile import build_eval_profile
    from factory.store import ExperimentStore, ensure_factory_dir

    project_path = Path(args.path)
    _emit_cli_event(project_path, "discover.started", {"path": str(project_path)})

    profile = introspect_project(project_path)
    eval_profile = build_eval_profile(profile)

    eval_spec = generate_eval_spec(profile, project_path)

    # Persist artifacts so detect_state can find them
    store = ExperimentStore(project_path)
    ensure_factory_dir(store.factory_dir)
    _run(store.save_eval_profile(eval_profile))
    write_eval_script(eval_profile, project_path)

    if eval_spec:
        (store.factory_dir / "eval_spec.json").write_text(
            json.dumps(eval_spec, indent=2) + "\n"
        )

    from factory.discovery.spec import generate_spec, resolve_spec

    spec_path, spec_source = resolve_spec(project_path)
    if spec_source == "absent":
        spec_content = generate_spec(project_path, profile)
        spec_path = store.factory_dir / "SPEC.md"
        spec_path.write_text(spec_content)
        spec_source = "generated"

    dims = [d.name for d in eval_profile.dimensions]
    _emit_cli_event(project_path, "discover.completed", {
        "language": profile.language,
        "framework": profile.framework,
        "dimensions": dims,
        "eval_spec_count": len(eval_spec),
    })

    output = {
        "project": profile.model_dump(),
        "eval_profile": eval_profile.model_dump(),
        "eval_spec": eval_spec,
        "spec": {"path": str(spec_path), "source": spec_source},
    }
    print(json.dumps(output, indent=2))

    if profile.discovered_evals:
        print("\nDiscovered project eval scripts:", file=sys.stderr)
        for e in profile.discovered_evals:
            print(f"  - {e.name}: {e.command}", file=sys.stderr)
        print(
            "\nTo use these as project-specific eval dimensions, add them to "
            "factory.md under ## Project Eval:",
            file=sys.stderr,
        )
        for e in profile.discovered_evals:
            print(f"  - name: {e.name}", file=sys.stderr)
            print(f"    command: {e.command}", file=sys.stderr)
            print("    parse: json", file=sys.stderr)

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore, ensure_factory_dir

    project_path = Path(args.path)
    store = ExperimentStore(project_path)

    factory_md = project_path / "factory.md"
    if not factory_md.exists():
        print("Error: factory.md not found. Create it first or use --reparse.", file=sys.stderr)
        return 1

    # Ensure .factory/ dir exists so reparse_config can write config.json
    ensure_factory_dir(store.factory_dir)
    config = _run(store.reparse_config())

    if args.reparse:
        print(f"Reparsed config: goal={config.goal!r}")
    else:
        _run(store.init(config))
        print(f"Initialized .factory/ — goal={config.goal!r}")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    from factory.notify.telegram import TelegramNotifier
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    records = _run(store.load_history())
    notifier = TelegramNotifier()
    _run(notifier.send_digest(project_path.name, records, None))
    print("Digest sent.")
    return 0


def cmd_study(args: argparse.Namespace) -> int:
    from factory.study import study_project

    project_path = Path(args.path)
    _emit_cli_event(project_path, "study.started", {})
    kwargs: dict[str, object] = {}
    projects_dir = getattr(args, "projects_dir", None)
    if projects_dir:
        kwargs["projects_dir"] = str(Path(projects_dir).expanduser().resolve())
    focus = getattr(args, "focus", None)
    summary = study_project(project_path, focus=focus, **kwargs)

    # Write to .factory/strategy/observations.md
    obs_path = project_path / ".factory" / "strategy" / "observations.md"
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    obs_path.write_text(summary)

    _emit_cli_event(project_path, "study.completed", {"chars": len(summary)})
    print(summary)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    """Append a structured event to .factory/events.jsonl."""
    import json as json_mod

    from factory.events import emit_event

    project_path = Path(args.path).resolve()
    event_type = args.event_type

    if args.data:
        try:
            data = json_mod.loads(args.data)
        except json_mod.JSONDecodeError as exc:
            print(f"Error: invalid JSON in --data: {exc}", file=sys.stderr)
            return 1
    else:
        data = {}

    emit_event(project_path, event_type, agent=args.agent, data=data)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Manage ~/.factory/config.toml."""
    sub = getattr(args, "config_command", None)
    if not sub:
        print("Usage: factory config {show,edit,migrate}")
        return 1

    if sub == "show":
        from factory.user_config import show_config

        reveal = getattr(args, "reveal", False)
        print(show_config(reveal=reveal))
        return 0

    if sub == "edit":
        from factory.user_config import CONFIG_PATH, ensure_config_file

        ensure_config_file()
        editor = os.environ.get("EDITOR", "vi")
        return subprocess.call([editor, str(CONFIG_PATH)])

    if sub == "migrate":
        from factory.user_config import migrate_env_to_config

        try:
            msg = migrate_env_to_config()
            print(msg)
            return 0
        except (ImportError, FileExistsError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    print(f"Unknown config subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_emit(args: argparse.Namespace) -> int:
    from factory.events import emit_event

    project_path = Path(args.project).resolve()
    data: dict = {}
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as e:
            print(f"Error: --data is not valid JSON: {e}", file=sys.stderr)
            return 1
    emit_event(project_path, args.event_type, agent=args.agent, data=data)
    return 0


def cmd_self_update(args: argparse.Namespace) -> int:
    """Self-update the factory CLI via uv tool upgrade."""
    from importlib.metadata import version as pkg_version

    try:
        version_before = pkg_version("remote-factory")
    except Exception:
        version_before = "unknown"

    print(f"Current version: {version_before}")
    print("Upgrading remote-factory...")

    result = subprocess.run(
        ["uv", "tool", "upgrade", "remote-factory"],
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    if result.returncode != 0:
        print("Upgrade failed.", file=sys.stderr)
        return 1

    # Re-check version (may not reflect in this process, but show what uv reported)
    try:
        version_after = pkg_version("remote-factory")
    except Exception:
        version_after = "unknown"

    print(f"Version after upgrade: {version_after}")
    if version_before == version_after:
        print("Already up to date.")
    else:
        print(f"Updated: {version_before} -> {version_after}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install Factory agents as Claude Code or Codex CLI agents."""
    from factory.agents.plugin import generate_agent_content, generate_codex_agent_toml, load_agent_config

    runner = getattr(args, "runner", "claude") or "claude"

    role_filter = getattr(args, "role", None)
    config = load_agent_config()

    if role_filter and role_filter not in config:
        print(f"Unknown role: {role_filter!r}", file=sys.stderr)
        print(f"Available roles: {', '.join(config)}", file=sys.stderr)
        return 1

    roles = [role_filter] if role_filter else list(config)

    if runner == "codex":
        agents_dir = Path.home() / ".codex" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for role in roles:
            content = generate_codex_agent_toml(role)
            agent_path = agents_dir / f"factory-{role}.toml"
            agent_path.write_text(content)
            print(f"  Installed factory-{role} -> {agent_path}")
        print()
        print("Usage:")
        print("  codex --agent factory-<role>              # from any project directory")
        print('  codex --agent factory-ceo "improve X"     # with initial prompt')
    else:
        agents_dir = Path.home() / ".claude" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for role in roles:
            content = generate_agent_content(role)
            agent_path = agents_dir / f"factory-{role}.md"
            agent_path.write_text(content)
            print(f"  Installed factory-{role} -> {agent_path}")
        print()
        print("Usage:")
        print("  claude --agent factory-<role>              # from any project directory")
        print('  claude --agent factory-ceo "improve X"     # with initial prompt')
        print()
        print("Or from within Claude Code, ask: \"use the factory-<role> agent\"")

    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    """Manage the user profile at ~/.factory/profile.md."""
    sub = getattr(args, "profile_command", None)
    if not sub:
        print("Usage: factory profile {build,show}")
        return 1

    if sub == "show":
        from factory.profile import load_profile
        profile = load_profile()
        if profile is None:
            print("No profile found. Run 'factory profile build' first.")
            return 1
        print(profile)
        return 0

    if sub == "build":
        from factory.profile import collect_evidence, save_profile, synthesize_profile
        from factory.registry import get_project_paths

        raw_paths = getattr(args, "paths", None)
        if raw_paths:
            project_paths = [Path(p).resolve() for p in raw_paths]
        else:
            project_paths = get_project_paths()
            if not project_paths:
                print("No registered projects found. Pass project paths explicitly.", file=sys.stderr)
                return 1

        evidence = collect_evidence(project_paths)
        dry_run = getattr(args, "dry_run", False)

        if dry_run:
            for section, content in evidence.items():
                print(f"\n{'=' * 60}")
                print(f"  {section}")
                print(f"{'=' * 60}")
                print(content or "(empty)")
            return 0

        from factory.agents.runner import resolve_prompt
        from factory.cli._helpers import _resolve_runner
        runner_name = _resolve_runner(args)
        profiler_prompt = resolve_prompt("profiler")
        profile_text = _run(synthesize_profile(evidence, runner_name, prompt=profiler_prompt))
        if profile_text.startswith("Profile synthesis failed"):
            print(profile_text, file=sys.stderr)
            return 1
        source_names = [p.name for p in project_paths]
        path = save_profile(profile_text, source_names, runner_name or "claude")
        print(f"Profile written to {path}")
        return 0

    print(f"Unknown profile subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_usage(args: argparse.Namespace) -> int:
    """Print per-agent token usage breakdown from events.jsonl."""
    from factory.events import load_events

    project_path = Path(args.path).resolve()
    events = load_events(project_path)

    agent_stats: dict[str, dict[str, float]] = {}
    for ev in events:
        if ev.get("type") != "agent.completed":
            continue
        data = ev.get("data", {})
        if "input_tokens" not in data:
            continue
        agent = ev.get("agent", "unknown") or "unknown"
        if agent not in agent_stats:
            agent_stats[agent] = {
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "total_cost_usd": 0.0,
                "calls": 0, "avg_cost": 0.0,
            }
        s = agent_stats[agent]
        s["input_tokens"] += data.get("input_tokens", 0)
        s["output_tokens"] += data.get("output_tokens", 0)
        s["cache_read_tokens"] += data.get("cache_read_tokens", 0)
        s["total_cost_usd"] += data.get("total_cost_usd", 0.0)
        s["calls"] += 1

    for s in agent_stats.values():
        if s["calls"] > 0:
            s["avg_cost"] = s["total_cost_usd"] / s["calls"]

    use_json = args.json

    if use_json:
        print(json.dumps(agent_stats, indent=2))
        return 0

    if not agent_stats:
        print("No agent usage data found.")
        return 0

    header = f"{'Agent':<16} {'Input':>10} {'Output':>10} {'Cache Read':>12} {'Cost':>10} {'Calls':>6} {'Avg Cost':>10}"
    print(header)
    print("-" * len(header))

    total_input = 0
    total_output = 0
    total_cache = 0
    total_cost = 0.0
    total_calls = 0

    for agent, s in sorted(agent_stats.items()):
        inp = int(s["input_tokens"])
        out = int(s["output_tokens"])
        cache = int(s["cache_read_tokens"])
        cost = s["total_cost_usd"]
        calls = int(s["calls"])
        avg = s["avg_cost"]
        print(f"{agent:<16} {inp:>10,} {out:>10,} {cache:>12,} ${cost:>9.4f} {calls:>6} ${avg:>9.4f}")
        total_input += inp
        total_output += out
        total_cache += cache
        total_cost += cost
        total_calls += calls

    print("-" * len(header))
    total_avg = total_cost / total_calls if total_calls > 0 else 0.0
    print(f"{'TOTAL':<16} {total_input:>10,} {total_output:>10,} {total_cache:>12,} ${total_cost:>9.4f} {total_calls:>6} ${total_avg:>9.4f}")

    return 0

