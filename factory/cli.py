"""CLI entry point — argparse subcommands wrapping library functions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import structlog
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

log = structlog.get_logger()
_WIZARD_INPUT_PATH = Path("~/.factory/wizard_input.md")

if TYPE_CHECKING:
    from factory.messages import Message


def _run(coro):  # noqa: ANN001, ANN202
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _read_target_branch(project_path: Path) -> str:
    """Read target branch from .factory/config.json, falling back to git detection."""
    config_path = project_path / ".factory" / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            tb = config.get("target_branch")
            if tb:
                return tb
        except (json.JSONDecodeError, OSError):
            pass
    from factory.worktree import detect_default_branch

    return detect_default_branch(project_path)


# ── banner ────────────────────────────────────────────────────


_DASHBOARD_PORT = 8420


def _dashboard_is_running(port: int = _DASHBOARD_PORT) -> bool:
    """Check if the dashboard is already listening on the given port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_dashboard(project_path: Path, port: int = _DASHBOARD_PORT) -> None:
    """Start the dashboard in the background if it's not already running.

    Prints the dashboard URL to stderr either way.
    """
    url = f"http://localhost:{port}"

    if _dashboard_is_running(port):
        print(f"  Dashboard: {url} (running)", file=sys.stderr)
        return

    # Determine projects directory (parent of the project)
    projects_dir = project_path.parent

    # Start dashboard as a detached background process
    cmd = [
        sys.executable, "-m", "factory", "dashboard",
        "--projects-dir", str(projects_dir),
        "--port", str(port),
        "--host", "0.0.0.0",
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach from parent process
    )
    print(f"  Dashboard: {url} (started)", file=sys.stderr)


def _print_banner(mode: str = "improve") -> None:
    """Print the Factory startup banner to stderr."""
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        if mode == "welcome":
            print("The Factory — Self-Evolving Meta-Harness", file=sys.stderr)
        else:
            print(f"Factory v2 — mode: {mode}", file=sys.stderr)
        return

    c = "\033[1;36m"  # bold cyan
    d = "\033[2m"      # dim
    r = "\033[0m"      # reset

    mode_line = "" if mode == "welcome" else f"{d}  Mode: {mode}{r}\n"
    banner = (
        f"\n{c}  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻{r}\n"
        f"{c}  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛{r}\n"
        f"{c}  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹ {r}\n"
        f"{d}  Self-Evolving Meta-Harness{r}\n"
        f"{mode_line}"
    )
    print(banner, file=sys.stderr)


# ── welcome wizard ─────────────────────────────────────────────


_BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _show_spinner(stop_event: threading.Event) -> None:
    """Braille spinner on stderr. Respects NO_COLOR."""
    use_color = not os.environ.get("NO_COLOR") and sys.stderr.isatty()
    idx = 0
    while not stop_event.is_set():
        frame = _BRAILLE_FRAMES[idx % len(_BRAILLE_FRAMES)]
        if use_color:
            sys.stderr.write(f"\r\033[2m  Thinking... {frame}\033[0m")
        else:
            sys.stderr.write(f"\r  Thinking... {frame}")
        sys.stderr.flush()
        idx += 1
        stop_event.wait(0.1)
    if use_color:
        sys.stderr.write("\r\033[2K")
    else:
        sys.stderr.write("\r" + " " * 30 + "\r")
    sys.stderr.flush()


def _safe_is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except (OSError, ValueError):
        return False


def _safe_is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except (OSError, ValueError):
        return False


def _quick_classify(user_input: str) -> list[dict[str, str]] | None:
    """Deterministic fast path for paths, files, and URLs. Returns None if LLM needed."""
    stripped = user_input.strip()

    expanded = Path(stripped).expanduser()
    if _safe_is_dir(expanded):
        factory_dir = expanded / ".factory"
        label_improve = "Improve this project"
        label_interactive = "Discuss what to work on first"
        cmd_interactive = f'factory ceo {shlex.quote(stripped)} --mode interactive'
        if _safe_is_dir(factory_dir):
            cmd_improve = f'factory ceo {shlex.quote(stripped)} --mode improve'
            return [
                {"label": label_improve, "explanation": "Run the improve loop on this project.", "command": cmd_improve},
                {"label": label_interactive, "explanation": "Study the project and discuss priorities.", "command": cmd_interactive},
            ]
        cmd_improve = f'factory ceo {shlex.quote(stripped)}'
        return [
            {"label": "Set up and improve this project", "explanation": "Initialize factory and start improving.", "command": cmd_improve},
            {"label": label_interactive, "explanation": "Study the project and discuss priorities.", "command": cmd_interactive},
        ]

    if _safe_is_file(expanded):
        if expanded == _WIZARD_INPUT_PATH.expanduser():
            return [
                {"label": "Build from this idea", "explanation": "Build the project directly.", "command": f'factory ceo {shlex.quote(stripped)} --mode build'},
                {"label": "Brainstorm and refine first", "explanation": "Discuss and refine the idea interactively.", "command": f'factory ceo {shlex.quote(stripped)} --mode interactive'},
            ]
        return [
            {"label": "Build from this spec file", "explanation": "Use the file as a project specification.", "command": f'factory ceo {shlex.quote(stripped)} --mode build'},
        ]

    if _is_github_url(stripped):
        return [
            {"label": "Clone and improve", "explanation": "Clone the repository and run the improve loop.", "command": f'factory ceo {shlex.quote(stripped)} --mode improve'},
            {"label": "Clone and discuss", "explanation": "Clone and discuss what to work on.", "command": f'factory ceo {shlex.quote(stripped)} --mode interactive'},
        ]

    return None


_WIZARD_PROMPT = """\
You are the Factory welcome wizard — a conversational CLI agent for Factory, \
a multi-agent software evolution tool.

Given the user's input, return a JSON object with two keys: "follow_ups" and "suggestions".

## Factory command vocabulary

| Command | When to use |
|---|---|
| `factory ceo "<idea>" --mode interactive` | Brainstorm and refine before building (vague ideas) |
| `factory ceo "<idea>"` | Build directly (clear, specific descriptions) |
| `factory ceo "<idea>" --mode research` | Research-driven optimization (metric-focused projects) |
| `factory ceo {path} --mode improve` | Improve an existing project at a known path |
| `factory ceo {path} --mode improve --focus "{issue}"` | Fix or add one specific thing in an existing project |
| `factory ceo {path} --mode improve --focus {issue}` | Target a specific GitHub issue number |
| `factory ceo {path} --mode interactive` | Discuss what to work on in an existing project |
| `factory ceo {path} --mode meta` | Self-improve the factory's own agents |

## Information requirements per mode

- **New idea** — just the idea text (already in the user input, no follow-ups needed)
- **Existing project** — `path` is required; `issue` is optional (ask if user mentions a bug/issue/fix)
- **Clone from URL** — URL already in user input (no follow-ups needed)
- **Meta** — `path` to the factory repo is required

## Follow-up question rules

- If the user mentions a specific repo/project name but didn't provide a path → ask for `path` (type: path)
- If the user says "fix", "issue", "bug", "problem" → ask which issue (type: issue)
- If the user's intent is clear and all info is present (e.g. pasted a URL, gave a complete idea) → \
no follow-ups needed (empty follow_ups array)
- If ambiguous → ask clarifying questions via follow_ups
- Mark follow-ups as `"optional": true` when the command works without them (e.g. issue number)
- Commands must use `{key}` placeholders matching follow_up keys

## Response format

Return ONLY a JSON object (no markdown, no explanation):

```
{
  "follow_ups": [
    {
      "key": "path",
      "question": "Path to your project",
      "type": "path",
      "hint": "e.g. ~/projects/my-app",
      "optional": false
    },
    {
      "key": "issue",
      "question": "Which issue? (number or description, leave blank to skip)",
      "type": "issue",
      "hint": "e.g. 42 or 'fix the login bug'",
      "optional": true
    }
  ],
  "suggestions": [
    {
      "label": "Fix specific issue",
      "explanation": "Target a known issue in the project",
      "command": "factory ceo {path} --mode improve --focus {issue}"
    },
    {
      "label": "Discuss first",
      "explanation": "Interactive mode to explore what needs fixing",
      "command": "factory ceo {path} --mode interactive"
    }
  ]
}
```

### Follow-up types

| Type | Validation |
|---|---|
| `path` | Must be an existing directory. Expand `~`, resolve to absolute. |
| `issue` | Numeric → `--focus N`. Text → `--focus "text"`. Empty → drop. |
| `text` | Any non-empty string (required unless optional). |
| `choice` | One of provided options (include "options" array in the follow_up). |

## Rules

1. The user's EXACT input must appear VERBATIM in quoted arguments — never summarize or shorten it
2. Return 2-3 suggestions
3. Each suggestion: {"label": "short title", "explanation": "one sentence why", "command": "factory ceo ..."}
4. First suggestion should be the most likely intent
5. You may add a "tip" field on the first suggestion with brief advice
6. For new ideas, commands should use the literal user text in quotes — no placeholders
7. For existing projects, use {path} placeholder and add a path follow-up
8. If the user mentions fixing/improving an EXISTING project, do NOT wrap input as a new idea
9. Every generated command MUST include an explicit `--mode` flag (improve, interactive, research, meta, or build)

User input: """


def _classify_with_llm(
    user_input: str,
) -> tuple[list[dict[str, object]], list[dict[str, str]]] | None:
    """Classify user input via headless runner call.

    Returns ``(follow_ups, suggestions)`` on success, ``None`` on failure.
    """
    from factory.runners import get_runner

    try:
        runner = get_runner()
    except Exception:
        return None

    prompt = _WIZARD_PROMPT + json.dumps(user_input)
    task = "Respond with ONLY a JSON object. No markdown, no explanation."

    try:
        stop_event = threading.Event()
        spinner = threading.Thread(target=_show_spinner, args=(stop_event,), daemon=True)
        spinner.start()

        old_quiet = os.environ.get("FACTORY_RUNNER_QUIET")
        os.environ["FACTORY_RUNNER_QUIET"] = "1"
        try:
            result, code = _run(runner.headless(
                prompt, task, Path.cwd(),
                timeout=60.0,
                dangerously_skip_permissions=True,
                role="wizard",
            ))
        finally:
            if old_quiet is None:
                os.environ.pop("FACTORY_RUNNER_QUIET", None)
            else:
                os.environ["FACTORY_RUNNER_QUIET"] = old_quiet

        stop_event.set()
        spinner.join(timeout=2.0)

        if code != 0:
            return None

        text = result.strip()

        # Determine whether the outermost JSON structure is an object or array.
        # Find the first meaningful JSON delimiter to pick the right parser.
        first_brace = text.find("{")
        first_bracket = text.find("[")

        # Try JSON array first if `[` appears before `{` (legacy format)
        if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
            arr_end = text.rfind("]")
            if arr_end != -1:
                try:
                    parsed_arr = json.loads(text[first_bracket:arr_end + 1])
                    if isinstance(parsed_arr, list) and len(parsed_arr) > 0:
                        for item in parsed_arr:
                            if not isinstance(item, dict) or "command" not in item or "label" not in item:
                                return None
                        return ([], parsed_arr[:3])
                except json.JSONDecodeError:
                    pass

        # Try parsing as a JSON object (new format)
        if first_brace != -1:
            obj_end = text.rfind("}")
            if obj_end != -1:
                try:
                    parsed = json.loads(text[first_brace:obj_end + 1])
                    if isinstance(parsed, dict) and "suggestions" in parsed:
                        suggestions = parsed["suggestions"]
                        follow_ups = parsed.get("follow_ups", [])
                        if not isinstance(suggestions, list) or len(suggestions) == 0:
                            return None
                        for item in suggestions:
                            if not isinstance(item, dict) or "command" not in item or "label" not in item:
                                return None
                        return (follow_ups[:10], suggestions[:3])
                except json.JSONDecodeError:
                    pass

        return None
    except Exception:
        stop_event.set()
        spinner.join(timeout=2.0)
        return None


_CLI_REF = """\
  Build something new:
    factory ceo "a fasta CLI that converts protein sequences to embeddings using ESM2" --mode interactive
    factory ceo "an autograd engine in pure numpy with a pytorch-like API" --mode interactive
    factory ceo "a system that solves IMO geometry problems using lean4 proofs" --mode research

  Work on an existing project:
    factory ceo ~/projects/my-app --mode improve --focus "add OAuth2 login with Google and GitHub providers"
    factory ceo ~/projects/my-app --mode improve --focus 42
    factory ceo ~/projects/my-app --mode interactive

  Self-improve the factory:
    factory ceo /path/to/factory --mode meta\
"""


def _ask_follow_ups(
    follow_ups: list[dict[str, object]],
    no_color: bool,
) -> dict[str, str] | None:
    """Ask follow-up questions and collect validated answers.

    Returns a dict mapping ``key`` to the user's answer, or ``None`` if
    the user pressed EOF/Ctrl+C.
    """
    if not follow_ups:
        return {}

    d = "\033[2m" if not no_color else ""
    r = "\033[0m" if not no_color else ""
    print(f"\n  {d}I'll need a few details:{r}", file=sys.stderr)

    answers: dict[str, str] = {}

    for fu in follow_ups:
        key = str(fu.get("key", ""))
        question = str(fu.get("question", key))
        fu_type = str(fu.get("type", "text"))
        hint = fu.get("hint", "")
        optional = bool(fu.get("optional", False))
        options = fu.get("options", [])

        # Build prompt
        opt_marker = " (optional)" if optional else ""
        hint_str = f" {d}{hint}{r}" if hint else ""
        if fu_type == "choice" and isinstance(options, list) and options:
            print(f"\n  {question}{opt_marker}", file=sys.stderr)
            for ci, opt in enumerate(options, 1):
                print(f"    {ci}. {opt}", file=sys.stderr)
            prompt_str = f"  [{1}-{len(options)}]: "
        else:
            prompt_str = f"\n  {question}{opt_marker}{hint_str}\n  > "

        try:
            raw = input(prompt_str).strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return None

        # Validate by type
        if fu_type == "path":
            if not raw:
                if optional:
                    continue
                print("  Path is required.", file=sys.stderr)
                return None
            expanded = Path(raw).expanduser().resolve()
            if not expanded.is_dir():
                print(f"  Not a directory: {expanded}", file=sys.stderr)
                return None
            answers[key] = shlex.quote(str(expanded))

        elif fu_type == "issue":
            if not raw:
                if optional:
                    continue
                print("  Issue is required.", file=sys.stderr)
                return None
            # Numeric issue → bare number, text → quoted
            if raw.isdigit():
                answers[key] = raw
            else:
                answers[key] = json.dumps(raw)  # produces "quoted text"

        elif fu_type == "choice":
            if not raw:
                if optional:
                    continue
                print("  A choice is required.", file=sys.stderr)
                return None
            if isinstance(options, list) and options:
                try:
                    idx = int(raw) - 1
                except ValueError:
                    print(f"  Invalid choice: {raw}", file=sys.stderr)
                    return None
                if idx < 0 or idx >= len(options):
                    print(f"  Invalid choice: {raw}", file=sys.stderr)
                    return None
                answers[key] = str(options[idx])
            else:
                answers[key] = raw

        else:  # text
            if not raw:
                if optional:
                    continue
                print("  This field is required.", file=sys.stderr)
                return None
            answers[key] = raw

    return answers


def _substitute_answers(
    suggestions: list[dict[str, str]],
    answers: dict[str, str],
) -> list[dict[str, str]]:
    """Substitute ``{key}`` placeholders in suggestion commands.

    Drops any suggestion that still has unfilled required placeholders after
    substitution (i.e. a ``{key}`` with no answer and the corresponding
    follow-up was not optional).
    """
    result: list[dict[str, str]] = []
    placeholder_re = re.compile(r"\{(\w+)\}")

    for s in suggestions:
        cmd = s.get("command", "")
        # Replace known answers
        for key, value in answers.items():
            cmd = cmd.replace(f"{{{key}}}", value)
        # Check for remaining placeholders
        remaining = placeholder_re.findall(cmd)
        if remaining:
            continue  # drop suggestions with unfilled placeholders
        result.append({**s, "command": cmd})

    return result


def _welcome_wizard() -> int:
    """Interactive welcome: banner -> input -> classify -> present -> dispatch."""
    no_color = bool(os.environ.get("NO_COLOR")) or not sys.stderr.isatty()

    _print_banner("welcome")

    if no_color:
        print("\n  What do you want to do?", file=sys.stderr)
        print("  Paste an idea, a file path, a GitHub URL, or describe what you need.\n", file=sys.stderr)
    else:
        d = "\033[2m"
        r = "\033[0m"
        print("\n  What do you want to do?", file=sys.stderr)
        print(f"  {d}Paste an idea, a file path, a GitHub URL, or describe what you need.{r}\n", file=sys.stderr)

    try:
        user_input = input("  > ").strip()
    except EOFError:
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130

    if not user_input:
        print(file=sys.stderr)
        print(_CLI_REF, file=sys.stderr)
        print(file=sys.stderr)
        try:
            user_input = input("  > ").strip()
        except EOFError:
            return 0
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
        if not user_input:
            return 0

    # -- long-input redirect -----------------------------------------------
    _expanded_check = Path(user_input).expanduser()
    if (
        len(user_input) > 200
        and not _safe_is_dir(_expanded_check)
        and not _safe_is_file(_expanded_check)
        and not _is_github_url(user_input)
    ):
        wizard_file = _WIZARD_INPUT_PATH.expanduser()
        wizard_file.parent.mkdir(parents=True, exist_ok=True)
        wizard_file.write_text(user_input)
        log.info("wizard.long_input_redirect", file=str(wizard_file), length=len(user_input))
        user_input = str(wizard_file)

    # -- classification ---------------------------------------------------
    follow_ups: list[dict[str, object]] = []
    suggestions: list[dict[str, str]] | None = _quick_classify(user_input)

    if suggestions is None:
        llm_result = _classify_with_llm(user_input)
        if llm_result is not None:
            follow_ups, suggestions = llm_result
        else:
            suggestions = None

    if not suggestions:
        print(file=sys.stderr)
        print(_CLI_REF, file=sys.stderr)
        return 1

    # -- follow-ups -------------------------------------------------------
    if follow_ups:
        answers = _ask_follow_ups(follow_ups, no_color)
        if answers is None:
            return 0  # EOF or Ctrl+C during follow-ups
        suggestions = _substitute_answers(suggestions, answers)
        if not suggestions:
            print("\n  No commands available after follow-up (required info missing).", file=sys.stderr)
            return 1

    # -- present suggestions ----------------------------------------------
    print(file=sys.stderr)

    tip = None
    for i, s in enumerate(suggestions, 1):
        label = s.get("label", "Option")
        explanation = s.get("explanation", "")
        command = s.get("command", "")
        if no_color:
            print(f"  [{i}] {label}", file=sys.stderr)
            if explanation:
                print(f"      {explanation}", file=sys.stderr)
            print(f"      {command}", file=sys.stderr)
        else:
            b = "\033[1m"
            d = "\033[2m"
            r = "\033[0m"
            print(f"  {b}[{i}]{r} {label}", file=sys.stderr)
            if explanation:
                print(f"      {d}{explanation}{r}", file=sys.stderr)
            print(f"      {command}", file=sys.stderr)
        if i == 1 and "tip" in s:
            tip = s["tip"]
        print(file=sys.stderr)

    if tip:
        if no_color:
            print(f"  Tip: {tip}", file=sys.stderr)
        else:
            print(f"  {d}Tip: {tip}{r}", file=sys.stderr)
        print(file=sys.stderr)

    prompt_text = f"  Pick [1-{len(suggestions)}], or Enter for [1]: "
    try:
        choice_raw = input(prompt_text).strip()
    except EOFError:
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130

    if not choice_raw:
        choice_idx = 0
    else:
        try:
            choice_idx = int(choice_raw) - 1
        except ValueError:
            print(f"\n  Invalid choice: {choice_raw}", file=sys.stderr)
            return 1

    if choice_idx < 0 or choice_idx >= len(suggestions):
        print(f"\n  Invalid choice: {choice_raw}", file=sys.stderr)
        return 1

    selected = suggestions[choice_idx]
    command = selected.get("command", "")

    print(f"\n  Running: {command}\n", file=sys.stderr)

    # Parse the selected command and dispatch to cmd_ceo
    parser = build_parser()
    try:
        parts = shlex.split(command)
    except ValueError:
        print(f"  Error: could not parse command: {command}", file=sys.stderr)
        return 1

    if parts and parts[0] == "factory":
        parts = parts[1:]

    try:
        ns = parser.parse_args(parts)
    except SystemExit:
        print(f"  Error: invalid command: {command}", file=sys.stderr)
        return 1

    if ns.command in ("ceo", "study"):
        handler = cmd_ceo if ns.command == "ceo" else globals().get("cmd_study")
        if handler:
            return handler(ns)

    print(f"  Error: unexpected command type: {ns.command}", file=sys.stderr)
    return 1


# ── subcommand handlers ────────────────────────────────────────


def cmd_home(args: argparse.Namespace) -> int:
    """Print the factory package root (where templates/ lives)."""
    factory_home = Path(__file__).resolve().parent
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


def cmd_eval(args: argparse.Namespace) -> int:
    from factory.eval.runner import run_eval
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    config = _run(store.read_config())
    skip_project_eval = getattr(args, "skip_project_eval", False)
    _emit_cli_event(project_path, "eval.started", {"command": config.eval_command})
    score = _run(run_eval(
        config.eval_command, project_path, config.eval_threshold,
        project_eval=config.project_eval or None,
        eval_weights=config.eval_weights,
        skip_project_eval=skip_project_eval,
    ))
    _emit_cli_event(project_path, "eval.completed", {
        "composite": score.total,
        "passed": score.passed,
        "dimensions": len(score.results),
    })
    print(json.dumps(score.model_dump(), indent=2, default=str))
    return 0 if score.passed else 1


def cmd_guard(args: argparse.Namespace) -> int:
    from factory.eval.guards import check_all

    project_path = Path(args.path)

    # Optionally load scope and fixed surfaces from factory config
    scope = None
    fixed_surfaces = None
    if args.check_scope or args.check_surfaces:
        from factory.store import ExperimentStore
        store = ExperimentStore(project_path)
        config = _run(store.read_config())
        if args.check_scope:
            scope = config.scope
        if args.check_surfaces:
            fixed_surfaces = config.fixed_surfaces

    violations = check_all(
        project_path, args.baseline, allowed_scope=scope, fixed_surfaces=fixed_surfaces,
    )
    _emit_cli_event(project_path, "guard.completed", {
        "violations": len(violations),
        "clean": len(violations) == 0,
    })
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("clean")
    return 0


def cmd_begin(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    exp_id = _run(store.begin(args.hypothesis))
    _emit_cli_event(project_path, "experiment.begin", {
        "exp_id": exp_id,
        "hypothesis": args.hypothesis[:200],
    })
    print(exp_id)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    from factory.precheck import run_precheck
    from factory.store import ExperimentStore
    from factory.models import ExperimentRecord, FactoryConfig

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    score_before = getattr(args, "score_before", None)
    score_after = getattr(args, "score_after", None)
    verdict = args.verdict
    notes = args.notes or ""

    force = getattr(args, "force", False)

    if verdict == "keep" and not force:
        config_path = project_path / ".factory" / "config.json"
        if config_path.exists():
            config = FactoryConfig(**json.loads(config_path.read_text()))
            history = _run(store.load_history())
            history_dicts = [r.model_dump() for r in history]

            precheck_result = run_precheck(
                score_before=score_before,
                score_after=score_after,
                threshold=config.eval_threshold,
                hypothesis=args.hypothesis or "",
                history=history_dicts,
                project_path=project_path,
                smoke_test_command=config.smoke_test,
                hard_constraints=config.hard_constraints,
            )

            if not precheck_result.passed:
                verdict = "revert"
                failure_detail = "; ".join(precheck_result.blocking_failures)
                notes = f"[OVERRIDDEN by finalize gate] precheck failed: {failure_detail}. {notes}"
                _emit_cli_event(project_path, "verdict.overridden", {
                    "exp_id": args.id,
                    "original_verdict": "keep",
                    "new_verdict": "revert",
                    "reason": failure_detail,
                })
                print(f"Finalize gate: precheck FAILED — overriding keep to revert ({failure_detail})")

    if verdict == "keep" and force:
        _emit_cli_event(project_path, "verdict.force_kept", {
            "exp_id": args.id,
        })
        print("Finalize gate: precheck SKIPPED (--force)")

    record = ExperimentRecord(
        id=args.id,
        timestamp=datetime.now(),
        hypothesis=args.hypothesis or "",
        change_summary=args.summary or "",
        issue_number=args.issue,
        pr_number=args.pr,
        score_before=score_before,
        score_after=score_after,
        delta=None,
        verdict=verdict,
        cost_usd=args.cost,
        notes=notes,
    )
    _run(store.finalize(args.id, record))
    _emit_cli_event(project_path, "experiment.finalize", {
        "exp_id": args.id,
        "verdict": verdict,
        "hypothesis": (args.hypothesis or "")[:200],
    })
    print(f"Finalized experiment {args.id} — verdict={verdict}")
    return 0


def cmd_message(args: argparse.Namespace) -> int:
    """Queue a message for the CEO agent."""
    from factory.messages import write_message

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Error: project path does not exist: {project_path}", file=sys.stderr)
        return 1
    if not (project_path / ".factory").exists():
        print(f"Error: not a factory project (no .factory/ directory): {project_path}", file=sys.stderr)
        return 1
    if not args.text or not args.text.strip():
        print("Error: message text must not be empty.", file=sys.stderr)
        return 1
    try:
        msg = write_message(project_path, args.text)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Message queued (id={msg.id}). The CEO will see it at the start of the next cycle.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore
    from factory.strategy import format_tiered_history

    store = ExperimentStore(Path(args.path))
    records = _run(store.load_history())
    if not records:
        print("No experiments recorded.")
        return 0

    record_dicts = [
        {
            "id": r.id,
            "hypothesis": r.hypothesis,
            "verdict": r.verdict,
            "delta": r.delta,
            "change_summary": r.change_summary,
            "cost_usd": r.cost_usd,
        }
        for r in records
    ]
    print(format_tiered_history(record_dicts))
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


def cmd_backlog_remove(args: argparse.Namespace) -> int:
    from factory.study import remove_backlog_item

    project_path = Path(args.path)
    item_text = args.item
    if remove_backlog_item(project_path, item_text):
        print(f"Removed backlog item: {item_text}")
        return 0
    print(f"Backlog item not found: {item_text}", file=sys.stderr)
    return 1


def cmd_backlog_list(args: argparse.Namespace) -> int:
    from factory.study import _migrate_legacy_backlog, _parse_backlog_items, _persist_backlog_items

    project_path = Path(args.path)
    _migrate_legacy_backlog(project_path)
    items = _parse_backlog_items(project_path)
    if not items:
        print("No backlog items.")
        return 0
    _persist_backlog_items(project_path, items)
    for item in items:
        print(f"- {item}")
    return 0


def cmd_backlog_add(args: argparse.Namespace) -> int:
    from factory.study import add_backlog_item

    project_path = Path(args.path)
    item_text = args.item
    if add_backlog_item(project_path, item_text):
        print(f"Added backlog item: {item_text}")
        return 0
    print(f"Backlog item already exists: {item_text}", file=sys.stderr)
    return 1


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


def cmd_summary(args: argparse.Namespace) -> int:
    """Generate an end-of-session summary report."""
    from factory.summary import format_summary, generate_summary, save_summary

    project_path = Path(args.path).resolve()
    _emit_cli_event(project_path, "summary.started", {})
    summary = _run(generate_summary(project_path))
    output = format_summary(summary)
    _run(save_summary(project_path, summary))
    _emit_cli_event(project_path, "summary.completed", {
        "kept": len(summary.experiments_kept),
        "reverted": len(summary.experiments_reverted),
        "errored": len(summary.experiments_errored),
        "backlog": len(summary.backlog_remaining),
    })
    print(output)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export a complete project snapshot as JSON to stdout."""
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    factory_dir = project_path / ".factory"

    if not factory_dir.is_dir():
        print(f"Error: {factory_dir} does not exist. Run 'factory init' first.", file=sys.stderr)
        return 1

    store = ExperimentStore(project_path)

    # Read config
    try:
        config = _run(store.read_config())
        config_data = config.model_dump()
    except FileNotFoundError:
        config_data = None

    # Read eval profile
    eval_profile = _run(store.read_eval_profile())
    eval_profile_data = eval_profile.model_dump() if eval_profile else None

    # Read experiment history
    records = _run(store.load_history())
    experiments_data = [r.model_dump() for r in records]

    # Read strategy
    strategy = _run(store.read_strategy())

    # Assemble snapshot
    snapshot = {
        "config": config_data,
        "eval_profile": eval_profile_data,
        "experiments": experiments_data,
        "strategy": strategy,
        "meta": {
            "project_path": str(project_path),
            "timestamp": datetime.now().isoformat(),
            "factory_version": "0.1.0",
        },
    }

    json.dump(snapshot, sys.stdout, indent=2, default=str)
    print()  # trailing newline
    return 0


def cmd_report_update(args: argparse.Namespace) -> int:
    """Generate a performance report for a project."""
    from factory.report import save_performance_report

    project_path = Path(args.path).resolve()
    report_path = save_performance_report(project_path)
    print(f"Performance report written to {report_path}")
    return 0


def cmd_registry_list(args: argparse.Namespace) -> int:
    """List all registered factory-managed projects."""
    from factory.registry import list_projects

    projects = list_projects()
    if not projects:
        print("No registered projects. Projects are auto-registered when experiments begin.")
        return 0

    header = f"{'Name':<30} {'Experiments':>11} {'Score':>8} {'Last Experiment':<20}"
    print(header)
    print("-" * len(header))
    for p in projects:
        score = f"{p.latest_score:.3f}" if p.latest_score is not None else "n/a"
        last = p.last_experiment_at.strftime("%Y-%m-%d %H:%M") if p.last_experiment_at else "never"
        print(f"{p.name:<30} {p.experiment_count:>11} {score:>8} {last:<20}")
    return 0


def cmd_ace(args: argparse.Namespace) -> int:
    """Run ACE self-improvement on agent playbooks."""
    from factory.ace.curator import curate_playbook
    from factory.ace.models import Playbook
    from factory.ace.paths import seed_user_playbooks, user_playbook_path, user_playbooks_dir
    from factory.ace.reflector import reflect_on_experiments, update_counters_from_experiments
    from factory.insights import discover_projects, load_all_histories

    project_path = Path(args.path).resolve()
    projects_dir_raw = getattr(args, "projects_dir", None)
    if projects_dir_raw:
        projects_dir = Path(projects_dir_raw).expanduser().resolve()
    else:
        from factory.registry import get_project_paths
        reg_paths = get_project_paths()
        if reg_paths:
            projects_dir = reg_paths[0].parent
        else:
            projects_dir = project_path.parent
    dry_run = getattr(args, "dry_run", False)

    _emit_cli_event(project_path, "ace.started", {"dry_run": dry_run})

    # Step 0: Update counters on existing playbooks from experiment verdicts
    user_dir = user_playbooks_dir()
    if not dry_run:
        seed_user_playbooks()
        project_paths = discover_projects(projects_dir)
        if project_path not in project_paths:
            project_paths.append(project_path)
        histories = load_all_histories(project_paths)
        all_records = [r for records in histories.values() for r in records]
        if all_records:
            update_counters_from_experiments(user_dir, all_records)

    # Step 1: Reflect — analyze experiment data, generate candidate bullets
    candidates = reflect_on_experiments(projects_dir, project_path)

    if not candidates:
        print("No candidate playbook bullets generated (not enough experiment data).")
        return 0

    # Step 2: Curate — merge with existing playbooks, prune
    roles_updated = []
    for role, items in candidates.items():
        playbook_path = user_playbook_path(role)
        if playbook_path.exists():
            existing = Playbook.from_markdown(playbook_path.read_text())
        else:
            existing = Playbook.empty(role)

        updated = curate_playbook(existing, items)

        if dry_run:
            print(f"\n{'=' * 60}")
            print(f"DRY RUN — {role} ({len(items)} candidates → {len(updated.items)} items)")
            print(f"{'=' * 60}")
            print(updated.to_markdown())
        else:
            playbook_path.write_text(updated.to_markdown())
            print(f"  {role}: {len(updated.items)} items → {playbook_path}")
            roles_updated.append(role)

    _emit_cli_event(project_path, "ace.completed", {
        "roles_updated": roles_updated,
        "candidates": len(candidates),
        "dry_run": dry_run,
    })

    if not dry_run:
        print(f"\nPlaybooks updated in {user_dir}")

    return 0


def cmd_ace_stats(args: argparse.Namespace) -> int:
    """Print a table of all playbook items with their helpful/harmful/net counters."""
    from factory.ace.models import Playbook
    from factory.ace.paths import DEFAULTS_DIR, user_playbooks_dir

    user_dir = user_playbooks_dir()

    all_items: list[tuple[str, str, int, int, int, str]] = []
    seen_roles: set[str] = set()

    # User-local playbooks take priority
    for playbook_path in sorted(user_dir.glob("*.md")):
        role = playbook_path.stem
        seen_roles.add(role)
        playbook = Playbook.from_markdown(playbook_path.read_text())
        for item in playbook.items:
            all_items.append((
                role,
                item.id,
                item.helpful,
                item.harmful,
                item.net_score,
                item.content[:60],
            ))

    # Fall back to defaults for roles without user-local
    for playbook_path in sorted(DEFAULTS_DIR.glob("*.md")):
        role = playbook_path.stem
        if role in seen_roles:
            continue
        playbook = Playbook.from_markdown(playbook_path.read_text())
        for item in playbook.items:
            all_items.append((
                role,
                item.id,
                item.helpful,
                item.harmful,
                item.net_score,
                item.content[:60],
            ))

    if not all_items:
        print("No playbook items found.")
        return 0

    # Print table header
    header = f"{'Role':<12} {'ID':<14} {'helpful':>7} {'harmful':>7} {'net':>5}  Text"
    print(header)
    print("-" * len(header))

    total_helpful = 0
    total_harmful = 0
    for role, item_id, helpful, harmful, net, text in all_items:
        print(f"{role:<12} {item_id:<14} {helpful:>7} {harmful:>7} {net:>5}  {text}")
        total_helpful += helpful
        total_harmful += harmful

    print("-" * len(header))
    print(
        f"Total: {len(all_items)} bullets, "
        f"helpful={total_helpful}, harmful={total_harmful}, "
        f"net={total_helpful - total_harmful}"
    )
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
    projects_dir_raw = getattr(args, "projects_dir", None)
    if projects_dir_raw:
        projects_dir = Path(projects_dir_raw).expanduser().resolve()
    else:
        from factory.registry import get_project_paths
        reg_paths = get_project_paths()
        if reg_paths:
            projects_dir = reg_paths[0].parent
        else:
            projects_dir = project_path.parent
    _emit_cli_event(project_path, "insights.started", {"projects_dir": str(projects_dir)})
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

    _emit_cli_event(project_path, "insights.completed", {
        "projects_analyzed": len(project_paths),
        "total_experiments": sum(len(h) for h in histories.values()),
    })
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

    project_path = Path(args.path).resolve()
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

    from factory.obsidian.notes import vault_path as get_vault_path

    vp = get_vault_path()
    _emit_cli_event(project_path, "archive.completed", {
        "experiments": len(records),
        "vault": str(vp) if vp else "none",
    })
    if vp:
        print(f"Archived {len(records)} experiments to {vp}")
    else:
        print(f"Archived {len(records)} experiments (vault not configured, skipped vault writes)")
    return 0


def cmd_precheck(args: argparse.Namespace) -> int:
    """Run hard precheck gate before keep/revert decision."""
    from factory.precheck import run_precheck
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    # Load history as dicts for anti-pattern matching
    records = _run(store.load_history())
    history = [
        {
            "id": r.id,
            "hypothesis": r.hypothesis,
            "verdict": r.verdict,
            "delta": r.delta,
        }
        for r in records
    ]

    result = run_precheck(
        score_before=args.score_before,
        score_after=args.score_after,
        threshold=config.eval_threshold,
        hypothesis=args.hypothesis or "",
        history=history,
        project_path=project_path,
        baseline_sha=args.baseline,
        allowed_scope=config.scope if args.baseline else None,
        smoke_test_command=config.smoke_test,
        similarity_threshold=args.similarity_threshold,
        fixed_surfaces=config.fixed_surfaces if config.fixed_surfaces else None,
    )

    # Output as JSON for machine consumption
    output = {
        "passed": result.passed,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in result.checks
        ],
        "blocking_failures": result.blocking_failures,
    }
    print(json.dumps(output, indent=2))

    _emit_cli_event(project_path, "precheck.completed", {
        "passed": result.passed,
        "failures": result.blocking_failures,
    })

    return 0 if result.passed else 1


def cmd_leakage_check(args: argparse.Namespace) -> int:
    """Check text for ground truth leakage against fixed surface fingerprints."""
    from factory.research.leakage import fingerprint_fixed_surfaces, scan_for_leakage
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    if not config.fixed_surfaces:
        print("SKIP: no fixed_surfaces configured in factory.md")
        return 0

    fingerprints = fingerprint_fixed_surfaces(project_path, config.fixed_surfaces)
    if not fingerprints:
        print("SKIP: no fixed surface files found to fingerprint")
        return 0

    text = args.text
    if args.text_file:
        text_path = Path(args.text_file)
        if not text_path.is_file():
            print(f"ERROR: text file not found: {args.text_file}")
            return 1
        text = text_path.read_text()
    elif args.text is None:
        import sys
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("ERROR: provide --text, --text-file, or pipe to stdin")
            return 1

    report = scan_for_leakage(text, fingerprints, args.sensitivity)

    output = {
        "flagged": report.flagged,
        "risk_level": report.risk_level,
        "findings": [
            {
                "source_file": f.source_file,
                "leaked_token": f.leaked_token,
                "context": f.context,
                "leak_type": f.leak_type,
            }
            for f in report.findings
        ],
    }
    print(json.dumps(output, indent=2))
    return 1 if report.risk_level in ("medium", "high") else 0


def cmd_validate_research(args: argparse.Namespace) -> int:
    """Validate research mode configuration for ground truth isolation."""
    from factory.research.leakage import validate_research_config
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    errors = validate_research_config(config, project_path)

    if not errors:
        print("VALID: research config passes all ground truth isolation checks")
        return 0

    for error in errors:
        print(f"ERROR: {error}")
    return 1


def cmd_refine_status(args: argparse.Namespace) -> int:
    """Print refinement state and regrounding output."""
    from factory.refine_state import format_status, read_state

    project_path = Path(args.path).resolve()
    state = read_state(project_path)
    print(format_status(state))
    return 0


def cmd_refine_begin(args: argparse.Namespace) -> int:
    """Record a new refinement entry and emit regrounding output."""
    from factory.refine_state import begin_refinement, format_begin

    project_path = Path(args.path).resolve()
    request = (args.request or "").strip()
    if not request:
        print("Error: --request must not be empty.", file=sys.stderr)
        return 1
    entry = begin_refinement(project_path, request)
    _emit_cli_event(project_path, "refine.begin", {
        "sequence": entry.sequence,
        "request": request[:200],
    })
    print(format_begin(entry))
    return 0


def cmd_refine_complete(args: argparse.Namespace) -> int:
    """Update the last refinement entry with a verdict."""
    from factory.refine_state import complete_refinement, read_state

    project_path = Path(args.path).resolve()
    verdict = args.verdict
    state = read_state(project_path)
    if not state.entries:
        print("Warning: no refinement entries found — nothing to complete.", file=sys.stderr)
        return 1
    last = state.entries[-1]
    mutated = complete_refinement(project_path, verdict)
    if not mutated:
        print(f"Warning: refinement #{last.sequence} is already completed.", file=sys.stderr)
        return 1
    _emit_cli_event(project_path, "refine.complete", {
        "sequence": last.sequence,
        "verdict": verdict,
    })
    print(f"Refinement #{last.sequence} completed — verdict: {verdict}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Format and optionally post a review on a GitHub PR."""
    from factory.review import ReviewPayload, format_review, post_review

    guard_results: dict[str, str] = {}
    if args.guards:
        for pair in args.guards.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                guard_results[k.strip()] = v.strip()

    payload = ReviewPayload(
        verdict=args.verdict.upper(),
        reason=args.reason or "",
        score_before=args.score_before,
        score_after=args.score_after,
        threshold=args.threshold,
        guard_results=guard_results,
        precheck_summary=args.precheck_summary or "",
        code_notes=[n.strip() for n in args.code_notes.split("|")] if args.code_notes else [],
        experiment_id=args.experiment_id,
        hypothesis=args.hypothesis or "",
    )

    review_body = format_review(payload)

    if args.pr and not args.dry_run:
        success = post_review(args.pr, review_body, payload.verdict, repo=args.repo)
        if success:
            print(f"Review posted on PR #{args.pr}")
        else:
            print(f"Failed to post review on PR #{args.pr}", file=sys.stderr)
            print(review_body)
            return 1
    else:
        print(review_body)

    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Show or save a checkpoint for crash-resilient resume."""
    from factory.checkpoint import (
        CheckpointState,
        clear_checkpoint,
        format_checkpoint,
        load_checkpoint,
        save_checkpoint,
    )

    project_path = Path(args.path).resolve()

    if args.clear:
        clear_checkpoint(project_path)
        print("Checkpoint cleared.")
        return 0

    if args.save:
        completed_hyps: list[int] = []
        if args.completed_hypotheses:
            completed_hyps = [int(x.strip()) for x in args.completed_hypotheses.split(",") if x.strip()]
        state = CheckpointState(
            mode=args.mode or "improve",
            active_experiment_id=args.experiment,
            completed_agents=[a.strip() for a in args.completed.split(",")] if args.completed else [],
            pending_agents=[a.strip() for a in args.pending.split(",")] if args.pending else [],
            last_eval_scores=json.loads(args.scores) if args.scores else {},
            current_hypothesis=args.hypothesis,
            completed_hypotheses=completed_hyps,
            timestamp=datetime.now().isoformat(),
        )
        save_checkpoint(project_path, state)
        print(f"Checkpoint saved to {project_path / '.factory' / 'checkpoint.json'}")
        return 0

    # Show current checkpoint
    loaded = load_checkpoint(project_path)
    if loaded is None:
        print("No checkpoint found.")
        return 0
    print(format_checkpoint(loaded))
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


def cmd_resume(args: argparse.Namespace) -> int:
    """Load checkpoint and display resume context for the CEO."""
    from factory.checkpoint import format_checkpoint, load_checkpoint

    project_path = Path(args.path).resolve()
    state = load_checkpoint(project_path)
    if state is None:
        print("No checkpoint found. Nothing to resume.")
        return 1

    print("=== Resume Context ===")
    print(format_checkpoint(state))
    print()
    print("The CEO should resume from this state, skipping completed agents")
    print(f"and continuing with: {', '.join(state.pending_agents) or 'none'}")
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """Print citation index table and coverage summary."""
    from factory.research_index import build_citation_index, citation_coverage
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    records = _run(store.load_history())

    if not records:
        print("No experiments recorded.")
        return 0

    index = build_citation_index(project_path)
    coverage = citation_coverage(project_path)

    # Print table
    header = f"{'ID':>4}  {'Hypothesis':<52}  Citations"
    print(header)
    print("-" * len(header))
    for r in records:
        hyp = r.hypothesis[:50]
        cites = index.get(r.id, [])
        cite_str = ", ".join(cites) if cites else "-"
        print(f"{r.id:>4}  {hyp:<52}  {cite_str}")

    # Summary
    cited_count = sum(1 for r in records if r.research_citations)
    print()
    print(f"{len(records)} experiments, {cited_count} cited, coverage {coverage:.0%}")
    return 0


def cmd_backfill_citations(args: argparse.Namespace) -> int:
    """Backfill citations from experiment text into .factory/citations.json."""
    from factory.research_index import backfill_citations

    project_path = Path(args.path).resolve()
    index = backfill_citations(project_path)
    print(f"Backfilled citations for {len(index)} experiments")
    for exp_id, cites in sorted(index.items(), key=lambda x: int(x[0])):
        print(f"  #{exp_id}: {', '.join(cites[:5])}")
    return 0


def cmd_backfill_archive(args: argparse.Namespace) -> int:
    """Generate archive notes for experiments missing from .factory/archive/experiments/."""
    from factory.backfill_archive import backfill_archive

    project_path = Path(args.path).resolve()
    result = _run(backfill_archive(project_path))
    print(
        f"Archive backfill complete: {result['existed']} existed, "
        f"{result['created']} created, {result['total']} total"
    )
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Compare two experiments side-by-side."""
    from factory.analysis import compare_experiments, format_comparison
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    comparison = compare_experiments(store, args.id_a, args.id_b)
    print(format_comparison(comparison))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain a single experiment with FEEC category and dimension breakdown."""
    from factory.analysis import explain_experiment, format_explanation
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    explanation = explain_experiment(store, args.id)
    print(format_explanation(explanation))
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


def cmd_vault_init(args: argparse.Namespace) -> int:
    from factory.obsidian.notes import init_vault

    vault_result = init_vault()
    if vault_result is None:
        print("No vault path configured. Set FACTORY_VAULT_PATH or run:")
        print("  export FACTORY_VAULT_PATH=~/factory-vault")
        print("  factory vault-init")
        return 1
    print(f"Factory vault initialized at {vault_result}")
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
    """Install Factory agents as Claude Code agents."""
    from factory.agents.plugin import generate_agent_content, load_agent_config

    agents_dir = Path.home() / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    role_filter = getattr(args, "role", None)
    config = load_agent_config()

    if role_filter and role_filter not in config:
        print(f"Unknown role: {role_filter!r}", file=sys.stderr)
        print(f"Available roles: {', '.join(config)}", file=sys.stderr)
        return 1

    roles = [role_filter] if role_filter else list(config)

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

        runner_name = _resolve_runner(args)
        profile_text = _run(synthesize_profile(evidence, runner_name))
        if profile_text.startswith("Profile synthesis failed"):
            print(profile_text, file=sys.stderr)
            return 1
        source_names = [p.name for p in project_paths]
        path = save_profile(profile_text, source_names, runner_name or "claude")
        print(f"Profile written to {path}")
        return 0

    print(f"Unknown profile subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_agent(args: argparse.Namespace) -> int:
    """Invoke a specialist agent with the given task."""
    from factory.agents.runner import invoke_agent
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    role = args.role
    task = args.task
    project_path = Path(args.project).resolve()
    timeout = getattr(args, "timeout", 600.0)
    model = _resolve_model(args)
    runner = _resolve_runner(args)
    use_profile = getattr(args, "use_profile", False)

    result, code = _run(invoke_agent(
        role,
        task,
        project_path,
        timeout=timeout,
        dangerously_skip_permissions=True,
        model=model,
        runner_name=runner,
        use_profile=use_profile,
    ))
    print(result)
    return code


def cmd_serve_mcp(args: argparse.Namespace) -> int:
    """Start the Factory MCP stdio server."""
    from factory.mcp_server import main as mcp_main

    mcp_main()
    return 0


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
    """Launch the Factory CEO agent to orchestrate a project.

    Default: interactive foreground session (user can see and interact).
    With --headless: pipe mode via claude -p (for scripting, cron, etc.).
    With --mode interactive: brainstorm an idea via research + Distiller before building.
    """
    from factory.agents.runner import resolve_prompt
    from factory.runners import get_runner
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    raw_path = getattr(args, "path", None)
    mode = getattr(args, "mode", "auto")
    headless = getattr(args, "headless", False)
    prompt_file = getattr(args, "prompt", None)
    focus = getattr(args, "focus", None)
    dir_name = getattr(args, "dir", None)

    if not raw_path:
        print("Error: provide a project path, GitHub URL, idea file, or prompt",
              file=sys.stderr)
        return 1

    no_github = getattr(args, "no_github", False)
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

    _interactive_is_existing = (
        mode == "interactive"
        and raw_path
        and _safe_is_dir(Path(raw_path).expanduser().resolve())
    )

    if mode == "interactive":
        if headless:
            print("Error: --mode interactive requires foreground mode "
                  "(incompatible with --headless)", file=sys.stderr)
            return 1
        if prompt_file:
            print("Error: --mode interactive and --prompt are mutually exclusive. "
                  "Interactive mode generates the spec; --prompt provides one.",
                  file=sys.stderr)
            return 1
        if focus and not _interactive_is_existing:
            print("Error: --mode interactive and --focus are mutually exclusive "
                  "for new ideas. To discuss a topic on an existing project, "
                  "pass the project path: factory ceo /path --mode interactive --focus \"topic\"",
                  file=sys.stderr)
            return 1

    if mode == "research":
        if prompt_file:
            print("Error: --mode research and --prompt are mutually exclusive. "
                  "Research ideation generates the spec; --prompt provides one.",
                  file=sys.stderr)
            return 1

    interactive_idea: str | None = None
    interactive_existing: bool = False
    research_ideation: str | None = None
    if mode == "interactive" and _interactive_is_existing:
        project_path, context = _resolve_input(raw_path, dir_name=dir_name)
        interactive_existing = True
    elif mode == "interactive":
        resolved_file = Path(raw_path).expanduser()
        if resolved_file.is_file():
            interactive_idea = resolved_file.read_text()
            slug = _slugify(dir_name) if dir_name else _slugify(resolved_file.stem.split("—")[0].strip())
            project_path = _dedupe_project_path(_get_projects_dir() / slug, interactive_idea)
            _ensure_repo(project_path)
            _persist_spec(project_path, interactive_idea)
            print(f"Idea file: {resolved_file.name}")
            print(f"Project directory: {project_path}")
        else:
            interactive_idea = raw_path
            slug = _slugify(dir_name) if dir_name else _extract_project_name(raw_path)
            project_path = _dedupe_project_path(_get_projects_dir() / slug, raw_path)
            _ensure_repo(project_path)
            _persist_spec(project_path, raw_path)
        context = None
    elif mode == "research" and not _safe_is_dir(resolved := Path(raw_path).expanduser()) and not _safe_is_file(resolved):
        # New research project from idea — enter research ideation
        if headless:
            print("Error: --mode research for new projects requires foreground mode "
                  "(incompatible with --headless)", file=sys.stderr)
            return 1
        if focus:
            print("Error: --focus cannot be used with research ideation for new projects. "
                  "--focus targets existing backlog items.", file=sys.stderr)
            return 1
        research_ideation = raw_path
        slug = _slugify(dir_name) if dir_name else _extract_project_name(raw_path)
        project_path = _dedupe_project_path(_get_projects_dir() / slug, raw_path)
        _ensure_repo(project_path)
        context = None
    else:
        project_path, context = _resolve_input(raw_path, dir_name=dir_name)
    if prompt_file:
        context = _read_prompt_file(project_path, prompt_file)
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
    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path, has_prompt=bool(prompt_file or context),
            force_fresh=force_fresh,
        )
    discover_only = getattr(args, "discover_only", False)
    min_growth = getattr(args, "min_growth", None)
    max_new = getattr(args, "max_new", None)
    branch = getattr(args, "branch", None)
    model = _resolve_model(args)
    runner_name = _resolve_runner(args)
    use_profile = getattr(args, "use_profile", False)

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
    if focus and mode not in ("improve", "research") and not interactive_existing:
        print(f"Error: --focus (targeted mode) only works in improve or research mode, got '{mode}'. "
              "The project must already be built before targeting specific items.", file=sys.stderr)
        return 1

    if interactive_existing:
        banner_mode = "interactive"
    elif mode in ("interactive", "research") and (interactive_idea or research_ideation):
        banner_mode = "ideation"
    else:
        banner_mode = mode
    _print_banner(banner_mode)
    _ensure_dashboard(project_path)

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
    wt_path, wt_branch = create_worktree(project_path, base_branch)

    if interactive_existing:
        ceo_mode = "build"
    elif mode == "interactive" or research_ideation:
        ceo_mode = "build"
    else:
        ceo_mode = mode
    task = _build_ceo_task(
        wt_path, ceo_mode, context, focus=focus, prompt_file=prompt_file,
        min_growth=min_growth, max_new=max_new, branch=branch,
        discover_only=discover_only, no_github=no_github,
        interactive_idea=interactive_idea,
        interactive_existing=interactive_existing,
        research_ideation=research_ideation,
        messages=pending,
        issue_number=issue_number,
        issue_url=issue_url,
        refine_request=refine_request,
    )

    session_name = _derive_session_name(
        focus=focus,
        interactive_idea=interactive_idea,
        research_ideation=research_ideation,
        raw_path=raw_path,
        project_path=project_path,
        mode=banner_mode,
    )

    if headless:
        # Non-interactive pipe mode (for scripting, cron, tmux)
        # Uses completion guard to auto-resume on premature exit
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
            )
        finally:
            remove_worktree(project_path, wt_path, wt_branch)

    # Interactive foreground mode: use subprocess.run so we can clean up the worktree.
    try:
        if pending_ids:
            print(
                f"Consuming {len(pending_ids)} message(s): {', '.join(pending_ids)}",
                file=sys.stderr,
            )
            mark_read(project_path, pending_ids)
        prompt = resolve_prompt("ceo", wt_path, use_profile=use_profile)
        runner = get_runner(runner_name)
        return runner.interactive_run(
            prompt, task, wt_path,
            model=model, role="ceo", dangerously_skip_permissions=True,
            session_name=session_name,
        )
    finally:
        remove_worktree(project_path, wt_path, wt_branch)


def _is_github_url(path: str) -> bool:
    """Return True if path looks like a GitHub URL."""
    return path.startswith("https://github.com/") or path.startswith("git@github.com:")


# ── universal input resolver ─────────────────────────────────


def _resolve_model(args: argparse.Namespace) -> str | None:
    """Resolve model: CLI flag > FACTORY_MODEL env var > config.toml > None."""
    from factory.user_config import resolve

    flag = (getattr(args, "model", None) or "").strip() or None
    return resolve("model", cli_value=flag, env_var="FACTORY_MODEL")


def _resolve_runner(args: argparse.Namespace) -> str | None:
    """Resolve runner: CLI flag > FACTORY_RUNNER env var > None (default to 'claude').

    Returns None to let get_runner() handle the default.
    """
    flag = (getattr(args, "runner", None) or "").strip()
    if flag:
        return flag
    return None


def _get_projects_dir() -> Path:
    from factory.user_config import resolve

    raw = resolve("projects_dir", env_var="FACTORY_PROJECTS_DIR", default=str(Path.home() / "factory-projects"))
    return Path(raw).expanduser() if raw else Path.home() / "factory-projects"


def _resolve_input(raw: str, dir_name: str | None = None) -> tuple[Path, str | None]:
    """Resolve any user input to (project_path, optional_context).

    Handles four input types in priority order:
    1. Existing directory → use directly
    2. Existing file → read as spec, create repo
    3. GitHub URL → clone
    4. Raw prompt → create repo, use prompt as spec
    """
    # 1. Existing directory
    expanded = Path(raw).expanduser()
    if _safe_is_dir(expanded):
        return expanded.resolve(), None

    # 2. Existing file (e.g. path to an idea/spec .md file)
    if _safe_is_file(expanded):
        idea_content = expanded.read_text()
        slug = _slugify(dir_name) if dir_name else _slugify(expanded.stem.split("\u2014")[0].strip())
        project_path = _dedupe_project_path(_get_projects_dir() / slug, idea_content)
        _ensure_repo(project_path)
        _persist_spec(project_path, idea_content)
        print(f"Idea file: {expanded.name}")
        print(f"Project directory: {project_path}")
        return project_path, idea_content

    # 3. GitHub URL
    if _is_github_url(raw):
        tmp_dir = tempfile.mkdtemp(prefix="factory-")
        subprocess.run(["git", "clone", raw, tmp_dir], check=True)
        print(f"Cloned {raw} → {tmp_dir}")
        return Path(tmp_dir).resolve(), None

    # 4. Raw prompt
    slug = _slugify(dir_name) if dir_name else _extract_project_name(raw)
    project_path = _dedupe_project_path(_get_projects_dir() / slug, raw)
    _ensure_repo(project_path)
    _persist_spec(project_path, raw)
    print(f"New project from prompt: {project_path}")
    return project_path, raw


_FILLER_WORDS = frozenset({
    "a", "an", "the", "that", "which", "with", "for", "and", "or", "to", "using",
    "comprehensive", "simple", "basic", "advanced", "new", "custom", "full",
    "complete", "modern", "robust", "scalable", "lightweight", "minimal",
    "fully", "featured", "production", "ready",
})

_VERB_RE = re.compile(
    r"^(build|create|make|implement|develop|design|write|add|set\s*up|construct|craft)\b\s*"
)


def _extract_project_name(description: str) -> str:
    """Extract a concise project name from a verbose description.

    Strips leading imperative verbs and filler words, then takes
    up to 4 whitespace-delimited tokens (hyphenated compounds like
    ``real-time`` count as one token).
    """
    text = description.lower().strip()
    text = _VERB_RE.sub("", text)
    words = [w for w in re.split(r"\s+", text) if w and w not in _FILLER_WORDS]
    name = "-".join(words[:4])
    return _slugify(name) if name else _slugify(description[:50])


def _extract_short_description(text: str, max_words: int = 6) -> str:
    """Extract a short lowercase phrase from idea text for session naming.

    Like ``_extract_project_name`` but keeps spaces and allows more words.
    """
    lowered = text.lower().strip()
    lowered = _VERB_RE.sub("", lowered)
    words = [w for w in re.split(r"\s+", lowered) if w and w not in _FILLER_WORDS]
    return " ".join(words[:max_words])


def _derive_session_name(
    *,
    focus: str | None = None,
    interactive_idea: str | None = None,
    research_ideation: str | None = None,
    raw_path: str | None = None,
    project_path: Path,
    mode: str = "improve",
) -> str:
    """Derive a human-readable session name from the best available context.

    Priority:
    1. Focus directive (most specific)
    2. Interactive idea / research ideation (new project from idea)
    3. Raw idea text (new project from raw prompt, not a path/URL)
    4. Fallback: mode + project directory name
    """
    prefix = "factory: "
    max_len = 60

    if focus:
        label = focus.lower()[:max_len - len(prefix)]
        return f"{prefix}{label}"

    idea = interactive_idea or research_ideation
    if idea:
        desc = _extract_short_description(idea)
        if desc:
            return f"{prefix}{desc}"[:max_len]

    if raw_path and not _safe_is_dir(Path(raw_path).expanduser()) \
            and not _safe_is_file(Path(raw_path).expanduser()) \
            and not _is_github_url(raw_path):
        desc = _extract_short_description(raw_path)
        if desc:
            return f"{prefix}{desc}"[:max_len]

    proj_name = project_path.resolve().name
    return f"{prefix}{mode} {proj_name}"[:max_len]


def _dedupe_project_path(project_path: Path, new_spec: str) -> Path:
    """Append a numeric suffix if the directory already holds a different project."""
    spec_path = project_path / ".factory" / "strategy" / "current.md"
    if not spec_path.exists():
        return project_path
    if new_spec.strip() in spec_path.read_text():
        return project_path
    base = project_path
    counter = 2
    while True:
        candidate = base.parent / f"{base.name}-{counter}"
        cand_spec = candidate / ".factory" / "strategy" / "current.md"
        if not cand_spec.exists():
            return candidate
        if new_spec.strip() in cand_spec.read_text():
            return candidate
        counter += 1


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:50].rstrip("-") or "factory-project"


def _ensure_repo(project_path: Path) -> None:
    """Create directory + git init (with initial commit) if needed."""
    project_path.mkdir(parents=True, exist_ok=True)
    if not (project_path / ".git").is_dir():
        subprocess.run(["git", "init"], cwd=project_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Factory", "-c", "user.email=factory@localhost",
             "commit", "--allow-empty", "-m", "Initial commit"],
            cwd=project_path, capture_output=True, check=True,
        )


def _read_prompt_file(project_path: Path, prompt_file: str) -> str:
    """Read a prompt file (absolute or relative to project) and persist it as the build spec.

    Always overwrites current.md — the user is explicitly passing a new phase prompt.
    """
    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = project_path / prompt_path
    if not prompt_path.exists():
        print(f"Error: prompt file not found: {prompt_path}", file=sys.stderr)
        sys.exit(1)
    content = prompt_path.read_text()
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    spec_path = strategy_dir / "current.md"
    spec_path.write_text(f"## Project Specification\n\n{content}\n")
    print(f"  Prompt: {prompt_path.name} → .factory/strategy/current.md", file=sys.stderr)
    return content


def _resolve_focus_issue(
    focus: str, project_path: Path,
) -> tuple[str, str, int, str] | None:
    """If *focus* looks like an issue ref, fetch it and return (title, context, number, url).

    Returns ``None`` when *focus* is a plain backlog-item name.
    Callers must check ``--no-github`` *before* calling this function.
    """
    from factory.issue import is_issue_ref

    if not is_issue_ref(focus):
        return None

    from factory.issue import fetch_issue, format_issue_as_spec

    issue_spec = fetch_issue(focus, project_path)
    context = format_issue_as_spec(issue_spec)

    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "current.md").write_text(
        f"## Project Specification\n\n{context}\n"
    )
    print(
        f"  Issue: #{issue_spec.number} → .factory/strategy/current.md",
        file=sys.stderr,
    )
    return issue_spec.title, context, issue_spec.number, issue_spec.url


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

    # Build the factory run command — propagate env vars, use bare `factory`
    run_cmd_parts = [
        f"export CLAUDE_CODE_USE_VERTEX={shlex.quote(os.environ.get('CLAUDE_CODE_USE_VERTEX', '1'))}",
        f"export CLOUD_ML_REGION={shlex.quote(os.environ.get('CLOUD_ML_REGION', ''))}",
        f"export ANTHROPIC_VERTEX_PROJECT_ID={shlex.quote(os.environ.get('ANTHROPIC_VERTEX_PROJECT_ID', ''))}",
        'export PATH="$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"',
    ]

    model = _resolve_model(args)
    run_args = f"factory run {project_path}"
    if args.mode:
        run_args += f" --mode {args.mode}"
    if args.loop:
        run_args += " --loop"
    if args.interval:
        run_args += f" --interval {args.interval}"
    if args.max_cycles is not None:
        run_args += f" --max-cycles {args.max_cycles}"
    if model:
        run_args += f" --model {shlex.quote(model)}"
    if getattr(args, "no_github", False):
        run_args += " --no-github"

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



def _has_research_target(project_path: Path) -> bool:
    """Check if project already has research_target configured."""
    try:
        from factory.store import ExperimentStore
        config = _run(ExperimentStore(project_path).read_config())
        return config.research_target is not None
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError):
        return False


def _auto_detect_mode(project_path: Path, has_prompt: bool = False, force_fresh: bool = False) -> str:
    """Detect the right mode based on project state.

    Checks for an in-flight cycle first — if one exists, returns its mode
    regardless of current project state (prevents mode flip on respawn).

    Args:
        project_path: Path to the project.
        has_prompt: True if a build spec is available.
        force_fresh: If True, ignores in-flight cycle and detects from scratch.

    When a build spec is available (--prompt, idea file, or raw prompt),
    no_factory routes to build (not discover).
    """
    from factory.ceo_completion import read_cycle_state
    from factory.models import ProjectState
    from factory.state import detect_state

    # Layer 2: Check for in-flight cycle (unless forced fresh)
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


def _build_ceo_task(
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
    interactive_idea: str | None = None,
    interactive_existing: bool = False,
    research_ideation: str | None = None,
    messages: list[Message] | None = None,
    issue_number: int | None = None,
    issue_url: str | None = None,
    refine_request: str | None = None,
) -> str:
    """Build the CEO agent task string from mode and optional context."""
    task = f"Project: {project_path}\nMode: {mode}"

    if messages:
        task += "\n\n## User Messages\n"
        task += "The user has sent the following directives. Treat these as HIGH PRIORITY:\n\n"
        for msg in messages:
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            task += f"**[{ts}]** {msg.text}\n\n"

    if interactive_existing:
        task += (
            f"\n\n## Interactive Ideation Mode (Phase 0)\n\n"
            f"**existing_project: true**\n\n"
            f"You are in interactive ideation mode on an **existing project** at `{project_path}`.\n\n"
            f"Before running any experiments, research the project (local study + external "
            f"best practices), distill an improvement spec through user feedback, then "
            f"transition to Improve mode. Follow the Phase 0: Ideation protocol in your "
            f"system prompt — the existing-project conditionals in I0, I1, and I4 apply.\n\n"
        )
        if focus:
            task += (
                f"**Focus topic (from --focus):** {focus}\n\n"
                f"The user wants to discuss this specific topic. Use it to seed the "
                f"research and spec, but be open to the user redirecting.\n"
            )
        else:
            task += (
                "No specific topic was provided. Study the project broadly — "
                "look at the backlog, eval scores, open issues, and recent history — "
                "then present your findings and recommendations.\n"
            )
    elif interactive_idea:
        task += (
            f"\n\n## Interactive Ideation Mode (Phase 0)\n\n"
            f"**Raw idea from user:** {interactive_idea}\n\n"
            f"You are in interactive ideation mode. Before building anything, "
            f"you must refine this idea into a complete spec through research "
            f"and iterative user feedback. Follow the Phase 0: Ideation protocol "
            f"in your system prompt.\n\n"
            f"After the user approves the final spec, persist it to "
            f".factory/strategy/current.md and proceed to Build mode.\n"
        )

    if research_ideation:
        task += (
            f"\n\n## Research Ideation Mode (Phase 0)\n\n"
            f"**Raw idea from user:** {research_ideation}\n\n"
            f"You are in research ideation mode. This is like interactive ideation, "
            f"but the Distiller MUST collect research configuration:\n"
            f"- Research Target (objective, metric, target value, run_command, result_path)\n"
            f"- Mutable Surfaces (files the Builder can modify)\n"
            f"- Fixed Surfaces (ground truth / eval files that must never be touched)\n"
            f"- Research Constraints (additional rules)\n"
            f"- Cost Budget (optional)\n\n"
            f"Follow the Phase 0: Ideation protocol, but tell the Distiller this is a "
            f"research project. After the user approves, persist the spec AND the research "
            f"config to .factory/strategy/current.md, then proceed to Build mode. "
            f"During Review mode (factory.md creation), populate the research sections "
            f"from the approved spec.\n"
        )

    if prompt_file:
        task += (
            f"\n\n## Directive\n\n"
            f"The user has provided a specific prompt file (`{prompt_file}`) as the build spec. "
            f"This is your primary instruction — read it at `.factory/strategy/current.md` and "
            f"execute exactly what it describes. Do not infer or improvise beyond what the prompt asks for."
        )

    if focus:
        task += f"\n\n## Focus Directive (Targeted Mode)\n\nTarget: {focus}\n\n"
        if issue_number:
            issue_label = f"#{issue_number}"
            if issue_url:
                issue_label += f" ({issue_url})"
            task += (
                f"This target is from issue {issue_label}. "
                f"The full issue spec has been written to `.factory/strategy/current.md`. "
                f"Read it for the complete requirements.\n\n"
            )
        task += (
            "Single-item mode. This target has been added to the backlog. "
            "The Strategist must generate exactly ONE hypothesis for this item. "
            "No other hypotheses this cycle — no additional backlog clearing, no new items.\n"
            "After this single experiment completes (keep or revert), skip to final archival. "
            "Do not loop back for more hypotheses.\n"
        )
        if issue_number:
            task += (
                f"\n## Issue Tracking\n\n"
                f"This cycle is working on issue #{issue_number}. "
                f"When finalizing, pass `--issue {issue_number}` to `factory finalize`."
            )

    if branch:
        task += (
            f"\n\n## Branch Override\n\n"
            f"Target branch for all PRs and merges: `{branch}`\n"
            f"The Builder should create experiment branches from `{branch}` and "
            f"target PRs against `{branch}`. After revert, checkout `{branch}` instead of main.\n"
        )

    if any(v is not None for v in (min_growth, max_new)):
        budget_lines = ["\n\n## Budget Override\n"]
        budget_lines.append("The user has overridden the hypothesis budget for this run:")
        if min_growth is not None:
            budget_lines.append(f"- **min_growth:** {min_growth} (guaranteed growth hypotheses)")
        if max_new is not None:
            budget_lines.append(f"- **max_new:** {max_new} (max new items added to backlog per cycle)")
        budget_lines.append("")
        budget_lines.append("Pass these overrides to the Strategist. They take precedence over "
                           "factory.md defaults and study-computed values.")
        task += "\n".join(budget_lines)

    if context:
        task += f"\n\n## Project Specification\n\n{context}"

    if mode == "build":
        task += (
            "\n\nRun Build mode: the project is new or incomplete. Follow the Build mode "
            "pipeline (B0-B6): Research → Strategy → Build phases → E2E verification. "
            "Do NOT skip to Improve mode — the project needs to be built first."
        )
    elif mode == "discover":
        if discover_only:
            task += (
                "\n\nRun Discover mode: introspect the project, auto-detect eval dimensions, "
                "and generate the eval harness. Then complete Review mode to initialize the "
                "factory. Do NOT run the Improve loop."
            )
        else:
            task += (
                "\n\nRun Discover mode: introspect the project, auto-detect eval dimensions, "
                "and generate the eval harness. Then complete Review mode: verify the eval "
                "harness works, mark as reviewed, and initialize the factory. "
                "After initialization, proceed to Improve mode for one experiment cycle."
            )
    elif mode == "meta":
        task += (
            "\n\nRun Meta mode: full self-improvement. First, run the complete Improve loop "
            "on this project (experiments, keep/revert decisions). Then run ACE playbook "
            "evolution for all agent roles using cross-project experiment data."
        )
    elif mode == "research":
        task += (
            "\n\nRun Research mode: the project has a research target defined in factory.md. "
            "Read the research_target from config.json to understand the objective, metric, "
            "target value, and run command. Each cycle: form a hypothesis to improve the "
            "metric, implement the change within mutable_surfaces only (leave fixed_surfaces "
            "untouched), run the research command, compare results against the target, and "
            "make a keep/revert decision. Respect research_constraints and cost_budget."
        )

    if no_github:
        task += (
            "\n\n## GitHub Operations Disabled\n\n"
            "The user has passed --no-github. Do NOT:\n"
            "- Create issues on GitHub\n"
            "- Create or post pull requests\n"
            "- Push to remote repositories\n"
            "- Clone from GitHub URLs\n\n"
            "Work locally only. When a GitHub operation would normally occur, "
            "skip it and note what was skipped in the experiment log."
        )

    if refine_request:
        task += (
            f"\n\n## Refinement Mode\n\n"
            f"**User's refinement request:** {refine_request}\n\n"
            f"You are in Refinement mode. Follow the `Mode: Refine` section in your "
            f"system prompt. The pipeline is:\n\n"
            f"1. Spawn the Refiner agent to classify and scope the request\n"
            f"2. If Tier 3 → exit, tell user to use full Improve mode\n"
            f"3. Begin experiment, create GitHub issue from Refiner's scoped task\n"
            f"4. Spawn Builder with the Refiner's task description\n"
            f"5. Run the FULL review pipeline (2d-review through 2h-final) — identical to Improve mode\n"
            f"6. Keep/revert verdict + finalize\n"
            f"7. Archivist (single batch)\n\n"
            f"Do NOT skip the review pipeline. Do NOT abbreviate any step.\n"
        )

    return task


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
) -> int:
    """After a cycle completes, re-detect state and chain into the next mode.

    This ensures builds and discoveries flow through the full pipeline
    automatically — Build → Discover → Review → Improve — without manual
    re-invocation. Returns 0 when one Improve cycle completes (or all
    chains are exhausted).
    """
    from factory.models import ProjectState
    from factory.state import detect_state

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
            project_path, next_mode, focus=focus,
            min_growth=min_growth, max_new=max_new, branch=branch,
            no_github=no_github, model=model, use_profile=use_profile,
        )
        if code != 0:
            return code
    return 0


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
    use_profile: bool = False,
) -> int:
    """Execute a single factory run cycle via the CEO agent. Returns 0 on success, 1 on error."""
    from factory.agents.runner import invoke_agent
    from factory.worktree import create_worktree, remove_worktree

    if focus:
        from factory.study import add_backlog_item
        add_backlog_item(project_path, focus)

    from factory.messages import mark_read, read_pending

    pending = read_pending(project_path)
    pending_ids = [m.id for m in pending]

    base_branch = branch or _read_target_branch(project_path)
    wt_path, wt_branch = create_worktree(project_path, base_branch)

    try:
        task = _build_ceo_task(
            wt_path, mode, context, focus=focus, prompt_file=prompt_file,
            min_growth=min_growth, max_new=max_new, branch=branch,
            discover_only=discover_only, no_github=no_github,
            messages=pending,
            issue_number=issue_number,
            issue_url=issue_url,
        )

        result, code = _run(invoke_agent(
            "ceo",
            task,
            wt_path,
            timeout=7200.0,
            dangerously_skip_permissions=True,
            model=model,
            use_profile=use_profile,
        ))

        if code == 0:
            if pending_ids:
                mark_read(project_path, pending_ids)

        print(result)
        return code
    finally:
        remove_worktree(project_path, wt_path, wt_branch)


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
    min_growth = getattr(args, "min_growth", None)
    max_new = getattr(args, "max_new", None)
    branch = getattr(args, "branch", None)
    model = _resolve_model(args)
    use_profile_flag = getattr(args, "use_profile", False)

    if prompt_file:
        context = _read_prompt_file(project_path, prompt_file)
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
    mode = getattr(args, "mode", "auto")
    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path, has_prompt=bool(prompt_file or context),
            force_fresh=force_fresh,
        )

    if focus and loop:
        print("Error: --focus (targeted mode) and --loop are mutually exclusive. "
              "Targeted mode builds exactly one item and exits.", file=sys.stderr)
        return 1
    if focus and prompt_file:
        print("Error: --focus (targeted mode) and --prompt are mutually exclusive. "
              "--focus builds one backlog item; --prompt executes a spec file.", file=sys.stderr)
        return 1
    if focus and mode not in ("improve", "research"):
        print(f"Error: --focus (targeted mode) only works in improve or research mode, got '{mode}'. "
              "The project must already be built before targeting specific items.", file=sys.stderr)
        return 1

    _print_banner(mode)
    _ensure_dashboard(project_path)

    from factory.worktree import prune_stale
    pruned = prune_stale(project_path)
    if pruned:
        print(f"  Cleaned {len(pruned)} stale worktree(s)", file=sys.stderr)

    budget_kwargs = dict(min_growth=min_growth, max_new=max_new, branch=branch)
    skip_improve = mode in ("improve", "meta") or discover_only

    if not loop:
        code = _run_single_cycle(
            project_path, mode, context, focus=focus, prompt_file=prompt_file,
            discover_only=discover_only, no_github=no_github, model=model,
            issue_number=issue_number,
            issue_url=issue_url,
            use_profile=use_profile_flag,
            **budget_kwargs,
        )
        if code != 0:
            return code
        return _chain_modes(
            project_path, focus=focus, already_improved=skip_improve,
            min_growth=min_growth, max_new=max_new, branch=branch,
            model=model, no_github=no_github, use_profile=use_profile_flag,
        )

    # Heartbeat loop mode
    interval: int = getattr(args, "interval", 1800)
    max_cycles: int | None = getattr(args, "max_cycles", None)
    shutdown_event = threading.Event()

    def _shutdown_handler(signum: int, frame: object) -> None:
        shutdown_event.set()

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

            _run_single_cycle(
                project_path, mode, context, focus=focus, prompt_file=prompt_file,
                discover_only=discover_only, no_github=no_github, model=model,
                issue_number=issue_number,
                issue_url=issue_url,
                use_profile=use_profile_flag,
                **budget_kwargs,
            )
            _chain_modes(
                project_path, focus=focus, already_improved=skip_improve,
                min_growth=min_growth, max_new=max_new, branch=branch,
                model=model, no_github=no_github, use_profile=use_profile_flag,
            )
            _emit_cli_event(project_path, "cycle.completed", {"cycle": cycle, "mode": mode})

            # Re-detect mode for next cycle (state may have advanced)
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
    p.add_argument("--skip-project-eval", action="store_true", default=False,
                    help="Skip user-defined project eval dimensions (run only hygiene + growth)")

    # guard
    p = sub.add_parser("guard", help="Check guard rules, print violations or 'clean'")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--baseline", required=True, help="Baseline commit SHA")
    p.add_argument("--check-scope", action="store_true", help="Also check file scope")
    p.add_argument("--check-surfaces", action="store_true",
                    help="Also check fixed surface constraints (research mode)")

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
    p.add_argument("--score-before", type=float, default=None, help="Eval score before change")
    p.add_argument("--score-after", type=float, default=None, help="Eval score after change")
    p.add_argument("--force", action="store_true", default=False,
                    help="Bypass precheck gate (for pre-existing failures)")

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
    p.add_argument(
        "--focus", default=None,
        help="Targeted mode: filter observations to a single backlog item",
    )

    # backlog-remove (alias: deferred-remove)
    p = sub.add_parser("backlog-remove", aliases=["deferred-remove"], help="Remove a completed backlog item")
    p.add_argument("path", help="Path to the project")
    p.add_argument("item", help="Exact text of the backlog item to remove")

    # backlog-list (alias: deferred-list)
    p = sub.add_parser("backlog-list", aliases=["deferred-list"], help="List pending backlog items")
    p.add_argument("path", help="Path to the project")

    # backlog-add
    p = sub.add_parser("backlog-add", help="Add a new item to the backlog")
    p.add_argument("path", help="Path to the project")
    p.add_argument("item", help="Text of the backlog item to add")

    # status
    p = sub.add_parser("status", help="Print project status summary")
    p.add_argument("path", help="Path to the project")

    # summary
    p = sub.add_parser("summary", help="Generate end-of-session summary report")
    p.add_argument("path", help="Path to the project")

    # leakage-check
    p = sub.add_parser("leakage-check", help="Scan text for ground truth leakage against fixed surfaces")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--text", default=None, help="Text to scan for leakage (hypothesis, strategy, etc.)")
    p.add_argument("--text-file", default=None, help="Path to file containing text to scan (safer for large diffs)")
    p.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium",
                    help="Sensitivity level (default: medium)")

    # validate-research
    p = sub.add_parser("validate-research", help="Validate research mode configuration for ground truth isolation")
    p.add_argument("path", help="Path to the project")

    # backfill-citations
    p = sub.add_parser("backfill-citations", help="Extract citations from experiment text into citations.json")
    p.add_argument("path", help="Path to the project")

    # backfill-archive
    p = sub.add_parser("backfill-archive", help="Generate archive notes for experiments missing from archive")
    p.add_argument("path", help="Path to the project")

    # research
    p = sub.add_parser("research", help="Print research citation index for experiments")
    p.add_argument("path", help="Path to the project")

    # diff
    p = sub.add_parser("diff", help="Compare two experiments side-by-side")
    p.add_argument("path", help="Path to the project")
    p.add_argument("id_a", type=int, help="First experiment ID")
    p.add_argument("id_b", type=int, help="Second experiment ID")

    # explain
    p = sub.add_parser("explain", help="Explain a single experiment with FEEC analysis")
    p.add_argument("path", help="Path to the project")
    p.add_argument("id", type=int, help="Experiment ID")

    # export
    p = sub.add_parser("export", help="Export complete project snapshot as JSON to stdout")
    p.add_argument("path", help="Path to the project")

    # insights
    p = sub.add_parser("insights", help="Cross-project analysis of experiment histories")
    p.add_argument("path", help="Path to the project (insights.md written here)")
    p.add_argument(
        "--projects-dir", default=None,
        help="Directory containing factory-managed projects (default: from registry or ~/factory-projects)",
    )

    # report-update
    p = sub.add_parser("report-update", help="Generate performance report for a project")
    p.add_argument("path", help="Path to the project")

    # registry-list
    sub.add_parser("registry-list", help="List all registered factory-managed projects")

    # ace
    p = sub.add_parser("ace", help="Run ACE self-improvement on agent playbooks")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--projects-dir", default=None,
        help="Directory containing factory-managed projects (default: from registry or ~/factory-projects)",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print candidates without writing playbooks",
    )

    # ace-stats
    sub.add_parser("ace-stats", help="Print playbook item counters for all roles")

    # digest
    p = sub.add_parser("digest", help="Summarize recent factory activity across projects")
    p.add_argument("--date", default=None, help="Show activity for a specific date (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")

    # archive
    p = sub.add_parser("archive", help="Write experiment notes to Obsidian vault")
    p.add_argument("path", help="Path to the project")

    # precheck
    p = sub.add_parser("precheck", help="Run hard precheck gate before keep/revert decision")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--score-before", type=float, default=None, help="Eval score before change")
    p.add_argument("--score-after", type=float, default=None, help="Eval score after change")
    p.add_argument("--hypothesis", default=None, help="Current experiment hypothesis")
    p.add_argument("--baseline", default=None, help="Baseline commit SHA for scope check")
    p.add_argument("--similarity-threshold", type=float, default=0.6,
                    help="Similarity threshold for anti-pattern detection (default: 0.6)")

    # refine-status
    p = sub.add_parser("refine-status", help="Print refinement state and regrounding output")
    p.add_argument("path", help="Path to the project")

    # refine-begin
    p = sub.add_parser("refine-begin", help="Record a new refinement and emit regrounding output")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--request", required=True, help="Summary of the user's refinement request")

    # refine-complete
    p = sub.add_parser("refine-complete", help="Complete the current refinement with a verdict")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--verdict", required=True, choices=["keep", "revert", "error", "tier3_exit"],
                    help="Refinement verdict")

    # review
    p = sub.add_parser("review", help="Format and post a structured review on a GitHub PR")
    p.add_argument("--verdict", required=True, choices=["keep", "revert", "KEEP", "REVERT"],
                    help="Review verdict")
    p.add_argument("--reason", default=None, help="One-sentence reason for the verdict")
    p.add_argument("--score-before", type=float, default=None, help="Score before change")
    p.add_argument("--score-after", type=float, default=None, help="Score after change")
    p.add_argument("--threshold", type=float, default=0.8, help="Eval threshold")
    p.add_argument("--guards", default=None,
                    help="Guard results as 'check:PASS,check:FAIL' pairs")
    p.add_argument("--precheck-summary", default=None, help="Precheck gate output summary")
    p.add_argument("--code-notes", default=None,
                    help="Code review notes separated by | (pipe)")
    p.add_argument("--experiment-id", type=int, default=None, help="Experiment ID")
    p.add_argument("--hypothesis", default=None, help="Experiment hypothesis text")
    p.add_argument("--pr", type=int, default=None, help="PR number to post review on")
    p.add_argument("--repo", default=None, help="GitHub repo (owner/name) for the PR")
    p.add_argument("--dry-run", action="store_true", default=False,
                    help="Print review without posting")

    # checkpoint
    p = sub.add_parser("checkpoint", help="Show or save a CEO checkpoint for crash-resilient resume")
    p.add_argument("path", help="Path to the project")
    ckpt_action = p.add_mutually_exclusive_group()
    ckpt_action.add_argument("--save", action="store_true", default=False, help="Save a checkpoint")
    ckpt_action.add_argument("--clear", action="store_true", default=False,
                              help="Clear the checkpoint file")
    p.add_argument("--mode", default=None, help="CEO mode (e.g. improve, build)")
    p.add_argument("--experiment", type=int, default=None, help="Active experiment ID")
    p.add_argument("--completed", default=None,
                    help="Comma-separated list of completed agent roles")
    p.add_argument("--pending", default=None,
                    help="Comma-separated list of pending agent roles")
    p.add_argument("--scores", default=None,
                    help="JSON dict of eval scores (e.g. '{\"tests\": 0.9}')")
    p.add_argument("--hypothesis", default=None, help="Current hypothesis text")
    p.add_argument("--completed-hypotheses", default=None,
                    help="Comma-separated list of completed experiment IDs (e.g. '1,2,3')")

    # resume
    p = sub.add_parser("resume", help="Load checkpoint and display resume context")
    p.add_argument("path", help="Path to the project")

    # log
    p = sub.add_parser("log", help="Append a structured event to .factory/events.jsonl")
    p.add_argument("path", help="Path to the project")
    p.add_argument("event_type", help="Event type (e.g. phase.research.completed)")
    p.add_argument("--data", help="JSON data payload")
    p.add_argument("--agent", help="Agent name to attribute the event to")

    # vault-init
    p = sub.add_parser("vault-init", help="Create the factory Obsidian vault")

    # message — send a directive to the CEO
    p = sub.add_parser("message", help="Send a message to the CEO for the next cycle")
    p.add_argument("path", help="Path to the project")
    p.add_argument("text", help="Message text")

    # self-update
    sub.add_parser("self-update", help="Upgrade the factory CLI to the latest version")

    # install — install Factory agents as Claude Code agents
    p = sub.add_parser("install", help="Install Factory agents as Claude Code agents (~/.claude/agents/)")
    p.add_argument(
        "--role",
        default=None,
        help="Install only a specific agent role (default: all)",
    )

    # serve-mcp — MCP stdio server
    sub.add_parser("serve-mcp", help="Start the Factory MCP stdio server")

    # dashboard — live web dashboard
    p = sub.add_parser("dashboard", help="Launch the live Factory dashboard")
    p.add_argument(
        "--projects-dir", default="~/factory-projects",
        help="Directory containing factory-managed projects (default: ~/factory-projects)",
    )
    p.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    p.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")

    # config — user configuration management
    config_parser = sub.add_parser("config", help="Manage ~/.factory/config.toml")
    config_sub = config_parser.add_subparsers(dest="config_command")
    p_show = config_sub.add_parser("show", help="Show resolved config (secrets masked)")
    p_show.add_argument("--reveal", action="store_true", default=False,
                        help="Show full secret values instead of masking")
    config_sub.add_parser("edit", help="Open config.toml in $EDITOR")
    config_sub.add_parser("migrate", help="Create starter config.toml from current env vars")

    # profile — user profile management
    profile_parser = sub.add_parser("profile", help="Manage the user profile at ~/.factory/profile.md")
    profile_sub = profile_parser.add_subparsers(dest="profile_command")
    p_build = profile_sub.add_parser("build", help="Collect evidence and synthesize user profile")
    p_build.add_argument("paths", nargs="*", default=None,
                         help="Project paths to collect evidence from (default: all registered)")
    p_build.add_argument("--dry-run", action="store_true", default=False,
                         help="Print collected evidence without running LLM synthesis")
    p_build.add_argument("--runner", choices=["claude", "bob", "codex"], default=None,
                         help="CLI backend to use for synthesis")
    profile_sub.add_parser("show", help="Print the current user profile")

    # emit — emit a structured event to .factory/events.jsonl
    p = sub.add_parser("emit", help="Emit a structured event to .factory/events.jsonl")
    p.add_argument("event_type", help="Event type (e.g. agent.started, agent.completed)")
    p.add_argument("--agent", default=None, help="Agent role name")
    p.add_argument("--project", default=".", help="Project path")
    p.add_argument("--data", default=None, help="JSON string of additional event data")

    # agent — invoke a specialist agent directly
    p = sub.add_parser("agent", help="Invoke a specialist agent with a task")
    p.add_argument("role", choices=["researcher", "strategist", "builder", "reviewer",
                                     "evaluator", "archivist", "distiller", "ceo",
                                     "failure_analyst", "refiner"],
                    help="Agent role to invoke")
    p.add_argument("--task", required=True, help="Task description for the agent")
    p.add_argument("--project", required=True, help="Path to the project")
    p.add_argument("--timeout", type=float, default=600.0,
                    help="Timeout in seconds (default: 600)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocess (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into the agent prompt")

    # ceo — launch the Factory CEO agent directly
    p = sub.add_parser("ceo", help="Launch the Factory CEO agent (interactive by default)")
    p.add_argument("path", nargs="?", default=None,
                    help="Project path, GitHub URL, idea file path, or prompt. "
                         "In interactive mode, pass a raw idea string")
    p.add_argument(
        "--prompt", default=None,
        help="Path to a prompt/spec file (absolute or relative to project). "
             "Loaded as the build spec into .factory/strategy/current.md",
    )
    p.add_argument(
        "--mode",
        choices=["auto", "auto-fresh", "build", "discover", "improve", "meta", "interactive", "research"],
        default="auto",
        help="Run mode: auto (default, respects in-flight cycle), auto-fresh (ignores in-flight cycle), "
             "build, discover, improve, meta, interactive (research + brainstorm → spec → build), "
             "or research (autonomous research optimization)",
    )
    p.add_argument(
        "--focus", default=None,
        help="Target a specific item: backlog name ('dashboard UI'), issue number (42), "
             "URL (https://github.com/o/r/issues/42), or shorthand (owner/repo#42). "
             "Issue refs are auto-detected and fetched via gh/glab CLI",
    )
    p.add_argument(
        "--dir", default=None,
        help="Working directory name for the new project (overrides auto-derived name from prompt or idea file). "
             "Ignored when pointing at an existing directory or GitHub URL.",
    )
    p.add_argument(
        "--headless", action="store_true", default=False,
        help="Run in pipe mode (non-interactive) instead of foreground",
    )
    p.add_argument(
        "--discover-only", action="store_true", default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--no-github", action="store_true", default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument("--min-growth", type=int, default=None,
                    help="Minimum guaranteed growth hypotheses (default: 2)")
    p.add_argument("--max-new", type=int, default=None,
                    help="Max new items added to backlog per cycle (default: 2)")
    p.add_argument("--branch", default=None,
                    help="Target branch for PRs (default: from factory.md, fallback: main)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--refine", default=None, metavar="REQUEST",
        help="Refinement mode: classify and implement a user-directed change. "
             "Mutually exclusive with --mode interactive, --mode research, --mode meta, --prompt, --focus",
    )
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into agent prompts")

    # run
    p = sub.add_parser("run", help="Run factory cycle (delegates to CEO agent)")
    p.add_argument("path", help="Project path, GitHub URL, idea file path, or prompt")
    p.add_argument(
        "--prompt", default=None,
        help="Path to a prompt/spec file (absolute or relative to project). "
             "Loaded as the build spec into .factory/strategy/current.md",
    )
    p.add_argument(
        "--mode",
        choices=["auto", "auto-fresh", "build", "discover", "improve", "meta", "research"],
        default="auto",
        help="Run mode: auto (default, respects in-flight cycle), auto-fresh (ignores in-flight cycle), "
             "build, discover, improve, meta, or research",
    )
    p.add_argument(
        "--focus", default=None,
        help="Target a specific item: backlog name ('dashboard UI'), issue number (42), "
             "URL (https://github.com/o/r/issues/42), or shorthand (owner/repo#42). "
             "Issue refs are auto-detected and fetched via gh/glab CLI",
    )
    p.add_argument(
        "--discover-only", action="store_true", default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--no-github", action="store_true", default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
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
    p.add_argument("--min-growth", type=int, default=None,
                    help="Minimum guaranteed growth hypotheses (default: 2)")
    p.add_argument("--max-new", type=int, default=None,
                    help="Max new items added to backlog per cycle (default: 2)")
    p.add_argument("--branch", default=None,
                    help="Target branch for PRs (default: from factory.md, fallback: main)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into agent prompts")

    # tmux — launch factory run in a detached tmux session
    p = sub.add_parser("tmux", help="Launch factory run in a detached tmux session")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--session", default=None, help="Custom tmux session name")
    p.add_argument(
        "--mode",
        choices=["auto", "auto-fresh", "build", "discover", "improve", "meta", "research"],
        default="auto",
        help="Run mode (default: auto, respects in-flight cycle)",
    )
    p.add_argument("--loop", action="store_true", default=False, help="Enable loop mode")
    p.add_argument("--interval", type=int, default=1800, help="Loop interval in seconds")
    p.add_argument("--max-cycles", type=int, default=None, help="Max cycles for loop mode")
    p.add_argument("--attach", action="store_true", default=False,
                    help="Attach to session after creating")
    p.add_argument(
        "--no-github", action="store_true", default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")

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
        if sys.stdin.isatty() and sys.stderr.isatty():
            return _welcome_wizard()
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
        "backlog-remove": cmd_backlog_remove,
        "deferred-remove": cmd_backlog_remove,
        "backlog-list": cmd_backlog_list,
        "deferred-list": cmd_backlog_list,
        "backlog-add": cmd_backlog_add,
        "status": cmd_status,
        "summary": cmd_summary,
        "research": cmd_research,
        "backfill-citations": cmd_backfill_citations,
        "backfill-archive": cmd_backfill_archive,
        "diff": cmd_diff,
        "explain": cmd_explain,
        "export": cmd_export,
        "insights": cmd_insights,
        "report-update": cmd_report_update,
        "registry-list": cmd_registry_list,
        "ace": cmd_ace,
        "ace-stats": cmd_ace_stats,
        "digest": cmd_digest,
        "archive": cmd_archive,
        "precheck": cmd_precheck,
        "leakage-check": cmd_leakage_check,
        "validate-research": cmd_validate_research,
        "refine-status": cmd_refine_status,
        "refine-begin": cmd_refine_begin,
        "refine-complete": cmd_refine_complete,
        "review": cmd_review,
        "checkpoint": cmd_checkpoint,
        "resume": cmd_resume,
        "log": cmd_log,
        "vault-init": cmd_vault_init,
        "message": cmd_message,
        "self-update": cmd_self_update,
        "install": cmd_install,
        "serve-mcp": cmd_serve_mcp,
        "dashboard": cmd_dashboard,
        "config": cmd_config,
        "profile": cmd_profile,
        "emit": cmd_emit,
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
