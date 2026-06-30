"""CLI entry point for the factory — argparse subcommands wrapping library functions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from collections.abc import Callable
from typing import TYPE_CHECKING

log = structlog.get_logger()
_WIZARD_INPUT_PATH = Path("~/.factory/wizard_input.md")

CEO_MODES = ["auto", "auto-fresh", "build", "discover", "improve", "meta", "design", "interactive", "research", "review", "qa", "create"]
RUN_MODES = ["auto", "auto-fresh", "build", "discover", "improve", "meta", "research"]

if TYPE_CHECKING:
    from factory.messages import Message


def _run(coro):  # noqa: ANN001, ANN202
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _detect_pr_number(project_path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number", "-q", ".number"],
            capture_output=True,
            timeout=10,
            cwd=project_path,
        )
        if result.returncode == 0:
            return int(result.stdout.decode().strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        pass
    return None


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
        label_design = "Discuss what to work on first"
        cmd_design = f'factory ceo {shlex.quote(stripped)} --mode design'
        if _safe_is_dir(factory_dir):
            cmd_improve = f'factory ceo {shlex.quote(stripped)} --mode improve'
            return [
                {"label": label_improve, "explanation": "Run the improve loop on this project.", "command": cmd_improve},
                {"label": label_design, "explanation": "Study the project and discuss priorities.", "command": cmd_design},
            ]
        cmd_improve = f'factory ceo {shlex.quote(stripped)}'
        return [
            {"label": "Set up and improve this project", "explanation": "Initialize factory and start improving.", "command": cmd_improve},
            {"label": label_design, "explanation": "Study the project and discuss priorities.", "command": cmd_design},
        ]

    if _safe_is_file(expanded):
        if expanded == _WIZARD_INPUT_PATH.expanduser():
            return None
        return [
            {"label": "Build from this spec file", "explanation": "Use the file as a project specification.", "command": f'factory ceo {shlex.quote(stripped)} --mode build'},
        ]

    if _is_github_url(stripped):
        return [
            {"label": "Clone and improve", "explanation": "Clone the repository and run the improve loop.", "command": f'factory ceo {shlex.quote(stripped)} --mode improve --clean-pr'},
            {"label": "Clone and discuss", "explanation": "Clone and discuss what to work on.", "command": f'factory ceo {shlex.quote(stripped)} --mode design --clean-pr'},
        ]

    return None


_WIZARD_PROMPT = """\
You are the Factory welcome wizard — a conversational CLI agent for Factory, \
a multi-agent software evolution tool.

Given the user's input, return a JSON object with two keys: "follow_ups" and "suggestions".

## Factory command vocabulary

| Command | When to use |
|---|---|
| `factory ceo "<idea>" --mode design` | Brainstorm and refine before building (vague ideas) |
| `factory ceo "<idea>"` | Build directly (clear, specific descriptions) |
| `factory ceo "<idea>" --mode research` | Research-driven optimization (metric-focused projects) |
| `factory ceo {path} --mode improve` | Improve an existing project at a known path |
| `factory ceo {path} --mode improve --focus "{issue}"` | Fix or add one specific thing in an existing project |
| `factory ceo {path} --mode improve --focus {issue}` | Target a specific GitHub issue number |
| `factory ceo {path} --mode design` | Discuss what to work on in an existing project |
| `factory ceo {path} --mode meta` | Self-improve the factory's own agents |
| `factory ceo {path} --mode create` | Create a new factory mode (workflow + skill) |

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
      "explanation": "Design mode to explore what needs fixing",
      "command": "factory ceo {path} --mode design"
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
9. Every generated command MUST include an explicit `--mode` flag (improve, design, research, meta, build, or create)
10. When the input is a GitHub URL (clone scenario), always append `--clean-pr` to the generated command

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

    wizard_path = _WIZARD_INPUT_PATH.expanduser()
    input_path = Path(user_input.strip()).expanduser()
    if input_path == wizard_path:
        try:
            file_content = wizard_path.read_text()
        except OSError:
            file_content = user_input
        prompt = (
            _WIZARD_PROMPT
            + json.dumps(file_content)
            + f"\n\nNote: The user's input was saved to the file {wizard_path}. "
            "Use this file path (not the raw text) in all generated factory commands."
        )
    else:
        prompt = _WIZARD_PROMPT + json.dumps(user_input)
    task = "Respond with ONLY a JSON object. No markdown, no explanation."

    try:
        stop_event = threading.Event()
        spinner = threading.Thread(target=_show_spinner, args=(stop_event,), daemon=True)
        spinner.start()

        old_quiet = os.environ.get("FACTORY_RUNNER_QUIET")
        os.environ["FACTORY_RUNNER_QUIET"] = "1"
        try:
            from factory.models import AgentRunRequest

            wizard_request = AgentRunRequest(
                prompt=prompt, task=task, cwd=Path.cwd(),
                timeout=60.0, skip_permissions=True, role="wizard",
            )
            run_result = _run(runner.headless(wizard_request))
            result, code = run_result.stdout, run_result.return_code
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
    factory ceo "a fasta CLI that converts protein sequences to embeddings using ESM2" --mode design
    factory ceo "an autograd engine in pure numpy with a pytorch-like API" --mode design
    factory ceo "a system that solves IMO geometry problems using lean4 proofs" --mode research

  Work on an existing project:
    factory ceo ~/projects/my-app --mode improve --focus "add OAuth2 login with Google and GitHub providers"
    factory ceo ~/projects/my-app --mode improve --focus 42
    factory ceo ~/projects/my-app --mode design

  Self-improve the factory:
    factory ceo /path/to/factory --mode meta

  Create a new factory mode:
    factory ceo /path/to/factory --mode create\
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
        test_timeout=config.test_timeout,
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
                hard_constraints=config.hard_constraints,
                exp_id=args.id,
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

    pr_number = args.pr
    if pr_number is None:
        pr_number = _detect_pr_number(project_path)

    cost = args.cost
    if cost is None:
        from factory.events import load_events, sum_agent_costs
        exp_events = load_events(project_path)
        exp_start = None
        for ev in reversed(exp_events):
            if ev.get("type") == "experiment.begin":
                ts_str = ev.get("timestamp")
                if ts_str:
                    exp_start = datetime.fromisoformat(ts_str)
                    break
        cost = sum_agent_costs(project_path, since=exp_start) or None

    record = ExperimentRecord(
        id=args.id,
        timestamp=datetime.now(),
        hypothesis=args.hypothesis or "",
        change_summary=args.summary or "",
        issue_number=args.issue,
        pr_number=pr_number,
        score_before=score_before,
        score_after=score_after,
        delta=None,
        verdict=verdict,
        cost_usd=cost,
        notes=notes,
    )
    _run(store.finalize(args.id, record))
    delta = None
    if score_before is not None and score_after is not None:
        delta = round(score_after - score_before, 6)
    _emit_cli_event(project_path, "experiment.finalize", {
        "exp_id": args.id,
        "verdict": verdict,
        "hypothesis": (args.hypothesis or "")[:200],
        "pr_number": pr_number,
        "issue_number": args.issue,
        "score_before": score_before,
        "score_after": score_after,
        "delta": delta,
        "cost_usd": cost,
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
        _emit_cli_event(project_path, "backlog.removed", {"item": item_text})
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
        _emit_cli_event(project_path, "backlog.added", {"item": item_text})
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


def cmd_clean_pr(args: argparse.Namespace) -> int:
    """Strip non-essential artifacts from a PR diff."""
    from factory.clean_pr import strip_pr_artifacts
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    base_branch = config.target_branch or "main"
    exp_id = getattr(args, "exp", None)

    include = config.clean_pr_include or None
    exclude = config.clean_pr_exclude or None

    keep, stripped = strip_pr_artifacts(
        project_path,
        include=include,
        exclude=exclude,
        base_branch=base_branch,
        exp_id=exp_id,
    )

    if not stripped:
        print("Nothing to strip — all files are essential.")
        return 0

    print(f"Kept {len(keep)} files, stripped {len(stripped)} files:")
    for f in stripped:
        print(f"  - {f}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Fetch stored eval baseline for a commit from the eval-data branch."""
    from factory.baseline import fetch_baseline

    project_path = Path(args.path).resolve()

    commit = getattr(args, "commit", None)
    if not commit:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", _read_target_branch(project_path)],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("Error: could not determine merge-base commit.", file=sys.stderr)
            return 1
        commit = result.stdout.strip()

    baseline = fetch_baseline(project_path, commit_sha=commit)
    if baseline is None:
        print(f"No baseline found for commit {commit[:12]}", file=sys.stderr)
        return 1

    print(json.dumps(baseline, indent=2, default=str))
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

    qa_body = ""
    if args.qa_body_file:
        body_path = Path(args.qa_body_file)
        if body_path.exists():
            qa_body = body_path.read_text().strip()

    payload = ReviewPayload(
        verdict=args.verdict.upper(),
        reason=args.reason or "",
        score_before=args.score_before,
        score_after=args.score_after,
        threshold=args.threshold,
        guard_results=guard_results,
        precheck_summary=args.precheck_summary or "",
        code_notes=[n.strip() for n in args.code_notes.split("|")] if args.code_notes else [],
        qa_body=qa_body,
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


def cmd_agent(args: argparse.Namespace) -> int:
    """Invoke a specialist agent with the given task."""
    from factory.agents.plugin import load_agent_config
    from factory.agents.runner import invoke_agent
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    role = args.role
    task = args.task
    project_path = Path(args.project).resolve()
    timeout = getattr(args, "timeout", 600.0)
    model = _resolve_model(args)
    if not model:
        agent_config = load_agent_config()
        if role in agent_config:
            model = agent_config[role].model or None
    runner = _resolve_runner(args)
    use_profile = getattr(args, "use_profile", False)
    tmux_persist = _resolve_tmux_persist(args)
    background = _resolve_background(args)
    if background and tmux_persist:
        print("Error: --bg and --tmux-persist are mutually exclusive.", file=sys.stderr)
        return 1
    review_tag = getattr(args, "review_tag", None)
    parent_span = getattr(args, "parent_session", None) or os.environ.get("FACTORY_PARENT_SPAN_ID")
    if parent_span:
        os.environ["FACTORY_PARENT_SPAN_ID"] = parent_span

    result, code = _run(invoke_agent(
        role,
        task,
        project_path,
        timeout=timeout,
        dangerously_skip_permissions=True,
        model=model,
        runner_name=runner,
        use_profile=use_profile,
        tmux_persist=tmux_persist,
        background=background,
        review_tag=review_tag,
    ))
    print(result)
    return code


def cmd_runners_list(args: argparse.Namespace) -> int:
    """List all available runners with metadata."""
    from factory.runners import get_all_runner_meta

    meta_list = get_all_runner_meta()
    use_json = getattr(args, "json", False)

    if use_json:
        import json as json_mod
        data = []
        for m in meta_list:
            data.append({
                "name": m.name,
                "display_name": m.display_name,
                "binary": m.binary,
                "install_hint": m.install_hint,
                "available": m.is_available(),
                "auth_ok": m.check_auth(),
                "supports_model_override": m.supports_model_override,
                "supports_interactive": m.supports_interactive,
                "supports_streaming": m.supports_streaming,
                "supports_usage_telemetry": m.supports_usage_telemetry,
                "supports_session_name": m.supports_session_name,
            })
        print(json_mod.dumps(data, indent=2))
        return 0

    if not meta_list:
        print("No runners registered.")
        return 0

    header = f"{'Name':<12} {'Display':<20} {'Binary':<12} {'Available':>9} {'Auth':>6}"
    print(header)
    print("-" * len(header))
    for m in meta_list:
        avail = "yes" if m.is_available() else "no"
        auth = "ok" if m.check_auth() else "missing"
        print(f"{m.name:<12} {m.display_name:<20} {m.binary:<12} {avail:>9} {auth:>6}")
    return 0


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
    With --mode design: brainstorm an idea via research + Strategist before building.
    """
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

    # ── review mode early exit ────────────────────────────────
    if mode == "review":
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

    # ── qa mode early exit ─────────────────────────────────────
    if mode == "qa":
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

    create_description: str | None = None
    design_idea: str | None = None
    design_existing: bool = False
    research_ideation: str | None = None
    deferred_spec: str | None = None
    needs_materialize = False
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
        # New research project from idea — enter research ideation
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

    import time as _time

    _ceo_start = _time.time()

    from factory.runners.claude import _make_ceo_message_emitter

    ceo_tailer = _start_ceo_tailer(
        wt_path, cycle_span_id, _ceo_start,
        on_line=_make_ceo_message_emitter(wt_path),
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

    # Interactive foreground mode: use subprocess.run so we can clean up the worktree.
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


def _start_ceo_tailer(
    wt_path: Path, cycle_span_id: str | None, start_time: float,
    on_line: Callable[[bytes], None] | None = None,
) -> object | None:
    """Create the CEO span eagerly and start a TranscriptTailer."""
    try:
        from factory.telemetry import TranscriptTailer, begin_span, flush, is_enabled

        trace_id = ""
        ceo_span_id = ""

        if cycle_span_id and is_enabled():
            trace_id = os.environ.get("FACTORY_TRACE_ID", "")
            if trace_id:
                span = begin_span(trace_id, cycle_span_id, "ceo")
                if span:
                    ceo_span_id = span
                    flush()

        if not trace_id and not on_line:
            return None

        tailer = TranscriptTailer(
            trace_id=trace_id,
            span_id=ceo_span_id,
            project_path=wt_path,
            session_start=start_time,
            on_line=on_line,
        )
        tailer.start()
        return tailer
    except Exception:
        return None


def _stop_ceo_tailer(tailer: object | None) -> None:
    """Stop the tailer, do final drain, and end the CEO span."""
    if tailer is None:
        return
    try:
        from factory.telemetry import end_span

        tailer.stop_and_drain()  # type: ignore[attr-defined]
        trace_id = os.environ.get("FACTORY_TRACE_ID", "")
        span_id = getattr(tailer, "span_id", None)
        if trace_id and span_id:
            end_span(trace_id, span_id, status="completed")
    except Exception:
        pass


def _is_github_url(path: str) -> bool:
    """Return True if path looks like a GitHub URL."""
    return path.startswith("https://github.com/") or path.startswith("git@github.com:")


# ── universal input resolver ─────────────────────────────────


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
    design_idea: str | None = None,
    research_ideation: str | None = None,
    raw_path: str | None = None,
    project_path: Path,
    mode: str = "improve",
) -> str:
    """Derive a human-readable session name from the best available context.

    Priority:
    1. Focus directive (most specific)
    2. Design idea / research ideation (new project from idea)
    3. Raw idea text (new project from raw prompt, not a path/URL)
    4. Fallback: mode + project directory name
    """
    prefix = "factory: "
    max_len = 60

    if focus:
        label = focus.lower()[:max_len - len(prefix)]
        return f"{prefix}{label}"

    idea = design_idea or research_ideation
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


def _materialize_project(project_path: Path, spec: str | None = None) -> None:
    """Create git repo and optionally persist spec. Single choke point for deferred creation."""
    _ensure_repo(project_path)
    if spec:
        _persist_spec(project_path, spec)


def _is_scaffold_only(project_path: Path) -> bool:
    """Return True if project_path is empty scaffolding that can be safely removed.

    A project is considered scaffold-only when it has exactly 1 git commit
    (the initial empty commit from _ensure_repo) and the only non-.git content
    is .factory/strategy/current.md.
    """
    if not project_path.is_dir():
        return False
    git_dir = project_path / ".git"
    if not git_dir.is_dir():
        return False
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=project_path, capture_output=True, text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "1":
        return False
    non_git = [
        p for p in project_path.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]
    allowed = {project_path / ".factory" / "strategy" / "current.md"}
    return all(p in allowed for p in non_git)


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
_TMUX_SESSIONS_FILE = Path("~/.factory/tmux_sessions.json").expanduser()


def _tmux_session_name(project_path: Path) -> str:
    """Derive a tmux session name from a project path."""
    path_hash = hashlib.sha1(str(project_path).encode()).hexdigest()[:6]
    return f"{_TMUX_SESSION_PREFIX}{project_path.name}-{path_hash}"


def _load_tmux_session_mapping() -> dict[str, str]:
    """Load the session→project mapping from ~/.factory/tmux_sessions.json."""
    if _TMUX_SESSIONS_FILE.exists():
        try:
            return json.loads(_TMUX_SESSIONS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_tmux_session_mapping(session: str, project_path: str) -> None:
    """Save a session→project mapping entry to ~/.factory/tmux_sessions.json."""
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
    """Build the 'factory ceo ...' command string from parsed args.

    Uses 'factory ceo' (not 'factory run') so the session inside tmux
    is interactive — the user can attach and interact with the CEO directly.
    --loop/--interval/--max-cycles are factory-run-only flags and are
    NOT forwarded to factory ceo.
    """
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

    # Create detached tmux session
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
    """Launch the re:factory persistent supervisor agent.

    Sets up the workspace, resolves the session ID, and replaces the current
    process with an interactive claude session via os.execvp.
    """
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
    prompt_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="refactory-prompt-", delete=False,
    )
    prompt_file.write(prompt)
    prompt_file.close()

    if is_new_session:
        cmd = [
            "claude",
            "--session-id", session_id,
            "--append-system-prompt-file", prompt_file.name,
            "--dangerously-skip-permissions",
        ]
    else:
        cmd = [
            "claude",
            "--resume", session_id,
            "--append-system-prompt-file", prompt_file.name,
            "--dangerously-skip-permissions",
        ]

    if model:
        cmd.extend(["--model", model])

    os.chdir(project_path)
    os.execvp("claude", cmd)
    return 0  # unreachable after execvp


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
    design_idea: str | None = None,
    design_existing: bool = False,
    research_ideation: str | None = None,
    messages: list[Message] | None = None,
    issue_number: int | None = None,
    issue_url: str | None = None,
    refine_request: str | None = None,
    clean_pr: bool = False,
    display_mode: str | None = None,
    create_description: str | None = None,
) -> str:
    """Build the CEO agent task string from mode and optional context."""
    shown_mode = display_mode if display_mode is not None else mode
    task = f"Project: {project_path}\nMode: {shown_mode}"

    if messages:
        task += "\n\n## User Messages\n"
        task += "The user has sent the following directives. Treat these as HIGH PRIORITY:\n\n"
        for msg in messages:
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            task += f"**[{ts}]** {msg.text}\n\n"

    if design_existing:
        task += (
            f"\n\n## Plan Loop (Interactive)\n\n"
            f"**existing_project: true**\n\n"
            f"You are in interactive planning mode on an **existing project** at `{project_path}`.\n\n"
            f"Run the Plan Loop (P0-P3) with interactive approval. Research the project "
            f"(local study + external best practices), synthesize an improvement spec "
            f"through user feedback, then transition to Improve mode.\n\n"
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
    elif design_idea:
        task += (
            f"\n\n## Plan Loop (Interactive)\n\n"
            f"**Raw idea from user:** {design_idea}\n\n"
            f"Run the Plan Loop (P0-P3) with interactive approval. "
            f"Research the space, synthesize a build plan, and refine it "
            f"through user feedback before building.\n\n"
            f"After the user approves the final plan, persist it to "
            f".factory/strategy/current.md and proceed to Build mode.\n"
        )

    if research_ideation:
        task += (
            f"\n\n## Plan Loop (Interactive)\n\n"
            f"**Raw idea from user:** {research_ideation}\n\n"
            f"**research_project: true**\n\n"
            f"Run the Plan Loop (P0-P3) with interactive approval. "
            f"This is a research project — the Strategist MUST collect research configuration:\n"
            f"- Research Target (objective, metric, target value, run_command, result_path)\n"
            f"- Mutable Surfaces (files the Builder can modify)\n"
            f"- Fixed Surfaces (ground truth / eval files that must never be touched)\n"
            f"- Research Constraints (additional rules)\n"
            f"- Cost Budget (optional)\n\n"
            f"After the user approves, persist the spec AND the research "
            f"config to .factory/strategy/current.md, then proceed to Build mode. "
            f"During Review mode (factory.md creation), populate the research sections "
            f"from the approved spec.\n"
        )

    if create_description:
        task += (
            f"\n\n## Create Mode (New Factory Mode)\n\n"
            f"**Mode description from user:**\n{create_description}\n\n"
            f"You are in Create mode — a meta-mode for creating new factory modes.\n\n"
            f"Follow the Create workflow (skills/workflow-create/SKILL.md):\n"
            f"1. Research existing workflow patterns and the user's intent\n"
            f"2. Synthesize a complete workflow specification\n"
            f"3. Present the spec to the user for interactive approval\n"
            f"4. Implement: workflow definition, SKILL.md, CLI wiring, tests\n"
            f"5. QA verification (graph validates, SKILL.md generates, CLI recognizes mode)\n"
            f"6. Open PR for review\n\n"
            f"The implementation targets THIS project (the factory codebase). "
            f"Key files to modify: factory/workflow/definitions.py, "
            f"factory/workflow/skill_export.py, factory/cli.py, tests/.\n"
        )

    if prompt_file:
        task += (
            f"\n\n## Directive\n\n"
            f"The user has provided a specific prompt file (`{prompt_file}`) as the build spec. "
            f"This is your primary instruction — read it at `.factory/strategy/current.md` and "
            f"execute exactly what it describes. Do not infer or improvise beyond what the prompt asks for."
        )

    if focus and not create_description:
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
            "\n\nRun Build mode: the project is new or incomplete. Run the Plan Loop "
            "(P0-P3) to produce an approved build plan, then follow the Build pipeline "
            "(B3-B6): Build phases → E2E verification. "
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
    elif mode == "create":
        task += (
            "\n\nRun Create mode: read `skills/workflow-create/SKILL.md` for the full "
            "step-by-step playbook. This mode creates a new factory mode (workflow + skill + "
            "CLI wiring + tests) from the user's description above."
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

    if clean_pr:
        task += (
            "\n\n## Clean PR Mode\n\n"
            "Clean PR mode is ACTIVE. After the final review gate (2h-final), "
            "run step 2i-clean before marking the PR ready:\n\n"
            "```bash\n"
            "factory clean-pr $PROJECT_PATH --exp $EXP_ID\n"
            "```\n\n"
            "This strips non-essential artifacts (eval scripts, benchmarks, .factory files) "
            "from the PR while preserving the full diff in the experiment archive. "
            "If stripping breaks tests, fall back to the full diff.\n"
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
    tmux_persist: bool = False,
    background: bool = False,
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
            tmux_persist=tmux_persist, background=background,
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
    clean_pr: bool = False,
    tmux_persist: bool = False,
    background: bool = False,
    run_id: str | None = None,
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
    wt_path, wt_branch = create_worktree(project_path, base_branch, run_id=run_id)

    from factory.skill_cache import ensure_skills
    ensure_skills(wt_path)

    try:
        task = _build_ceo_task(
            wt_path, mode, context, focus=focus, prompt_file=prompt_file,
            min_growth=min_growth, max_new=max_new, branch=branch,
            discover_only=discover_only, no_github=no_github,
            messages=pending,
            issue_number=issue_number,
            issue_url=issue_url,
            clean_pr=clean_pr,
        )

        result, code = _run(invoke_agent(
            "ceo",
            task,
            wt_path,
            timeout=7200.0,
            dangerously_skip_permissions=True,
            model=model,
            use_profile=use_profile,
            tmux_persist=tmux_persist,
            background=background,
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

    clean_pr_flag = getattr(args, "clean_pr", None)
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

    if not loop:
        code = _run_single_cycle(
            project_path, mode, context, focus=focus, prompt_file=prompt_file,
            discover_only=discover_only, no_github=no_github, model=model,
            issue_number=issue_number,
            issue_url=issue_url,
            use_profile=use_profile_flag,
            clean_pr=clean_pr_resolved,
            tmux_persist=tmux_persist,
            background=background,
            run_id=run_id,
            **budget_kwargs,
        )
        if code != 0:
            return code
        return _chain_modes(
            project_path, focus=focus, already_improved=skip_improve,
            min_growth=min_growth, max_new=max_new, branch=branch,
            model=model, no_github=no_github, use_profile=use_profile_flag,
            tmux_persist=tmux_persist,
            background=background,
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
                clean_pr=clean_pr_resolved,
                tmux_persist=tmux_persist,
                background=background,
                run_id=run_id,
                **budget_kwargs,
            )
            _chain_modes(
                project_path, focus=focus, already_improved=skip_improve,
                min_growth=min_growth, max_new=max_new, branch=branch,
                model=model, no_github=no_github, use_profile=use_profile_flag,
                tmux_persist=tmux_persist,
                background=background,
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


_COMMAND_GROUPS: list[tuple[str, list[str]]] = [
    ("Entry Points", [
        "ceo", "run", "tmux", "tmux-ls", "tmux-capture", "tmux-stop", "refactory", "dashboard",
        "agent",
    ]),
    ("Project Setup", ["home", "detect", "discover", "init"]),
    ("Experiment Lifecycle", [
        "begin", "finalize", "guard", "precheck", "log", "emit", "review",
    ]),
    ("Project Intelligence", [
        "eval", "history", "study", "status", "summary", "diff", "explain", "export",
        "research", "insights", "report-update", "baseline", "clean-pr",
    ]),
    ("Backlog & Refinement", [
        "backlog-add", "backlog-list", "backlog-remove", "deferred-list", "deferred-remove",
        "refine-status", "refine-begin", "refine-complete", "message",
    ]),
    ("Knowledge & Archive", [
        "archive", "vault-init", "backfill-citations", "backfill-archive",
    ]),
    ("Self-Evolution", ["ace", "ace-stats", "digest", "workflow"]),
    ("Configuration", [
        "config", "profile", "install", "self-update", "runners", "usage", "serve-mcp",
    ]),
    ("Validation & Recovery", [
        "leakage-check", "validate-research", "checkpoint", "resume", "notify", "registry-list",
    ]),
]


class _GroupedHelpParser(argparse.ArgumentParser):
    """ArgumentParser that renders subcommands in labelled groups."""

    def format_help(self) -> str:
        if self._subparsers is None:
            return super().format_help()

        sub_action: argparse._SubParsersAction | None = None  # type: ignore[type-arg]
        for action in self._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                sub_action = action
                break

        if sub_action is None:
            return super().format_help()

        parts = [f"usage: {self.prog} [-h] <command> ...\n"]
        if self.description:
            parts.append(f"{self.description}\n")

        help_map: dict[str, str] = {}
        for sub_act in sub_action._choices_actions:
            help_map[sub_act.dest] = sub_act.help or ""

        grouped_cmds: set[str] = set()
        for group_name, cmds in _COMMAND_GROUPS:
            lines = []
            for cmd in cmds:
                if cmd in sub_action._name_parser_map and cmd in help_map:
                    lines.append(f"  {cmd:25s}{help_map[cmd]}")
                    grouped_cmds.add(cmd)
            if lines:
                parts.append(f"\n{group_name}:\n" + "\n".join(lines))

        ungrouped = [
            c for c in help_map
            if c not in grouped_cmds and c in sub_action._name_parser_map
        ]
        if ungrouped:
            lines = [f"  {cmd:25s}{help_map[cmd]}" for cmd in ungrouped]
            parts.append("\nOther:\n" + "\n".join(lines))

        parts.append("")
        return "\n".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = _GroupedHelpParser(
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

    # clean-pr
    p = sub.add_parser("clean-pr", help="Strip non-essential artifacts from a PR diff")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--exp", type=int, default=None, help="Experiment ID (archives full diff before stripping)")

    # baseline
    p = sub.add_parser("baseline", help="Fetch stored eval baseline from eval-data branch")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--commit", default=None,
                    help="Commit SHA to look up (default: git merge-base HEAD <target-branch>)")

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
    p.add_argument("--qa-body-file", default=None,
                    help="Path to file containing QA analysis to include in review")
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

    # install — install Factory agents as Claude Code or Codex CLI agents
    p = sub.add_parser("install", help="Install Factory agents as CLI agents (~/.claude/agents/ or ~/.codex/agents/)")
    p.add_argument(
        "--role",
        default=None,
        help="Install only a specific agent role (default: all)",
    )
    p.add_argument(
        "--runner",
        choices=["claude", "codex"],
        default="claude",
        help="Target CLI: claude writes Markdown to ~/.claude/agents/, codex writes TOML to ~/.codex/agents/ (default: claude)",
    )

    # usage — token usage breakdown
    p = sub.add_parser("usage", help="Show per-agent token usage and cost breakdown")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--json", action="store_true", default=False,
                    help="Output as JSON instead of table")

    # runners — runner management
    runners_parser = sub.add_parser("runners", help="Manage factory runners")
    runners_sub = runners_parser.add_subparsers(dest="runners_command")
    p_runners_list = runners_sub.add_parser("list", help="List all registered runners")
    p_runners_list.add_argument("--json", action="store_true", default=False,
                                help="Output as JSON")

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
    p_build.add_argument("--runner", default=None,
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
    p.add_argument("role", choices=["researcher", "strategist", "builder", "qa",
                                     "archivist", "ceo",
                                     "failure_analyst", "refiner"],
                    help="Agent role to invoke")
    p.add_argument("--task", required=True, help="Task description for the agent")
    p.add_argument("--project", required=True, help="Path to the project")
    p.add_argument("--timeout", type=float, default=600.0,
                    help="Timeout in seconds (default: 600)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocess (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into the agent prompt")
    p.add_argument("--tmux-persist", action="store_true", default=False,
                    help="Run agent interactively in a tmux window instead of headless (claude only)")
    p.add_argument("--bg", action="store_true", default=False,
                    help="Dispatch agent as a background session via claude agent view (claude only)")
    p.add_argument("--review-tag", default=None,
                    help="Tag for distinct review output files (writes <role>-<tag>-latest.md)")
    p.add_argument("--parent-session", default=None,
                    help="Parent session ID for linking specialist sessions to a CEO cycle session")

    # ceo — launch the Factory CEO agent directly
    p = sub.add_parser("ceo", help="Launch the Factory CEO agent (interactive by default)")
    p.add_argument("path", nargs="?", default=None,
                    help="Project path, GitHub URL, idea file path, or prompt. "
                         "In design mode, pass a raw idea string")
    p.add_argument(
        "--prompt", default=None,
        help="Path to a prompt/spec file (absolute or relative to project). "
             "Loaded as the build spec into .factory/strategy/current.md",
    )
    p.add_argument(
        "--mode",
        choices=CEO_MODES,
        default="auto",
        help="Run mode: auto (default, respects in-flight cycle), auto-fresh (ignores in-flight cycle), "
             "build, discover, improve, meta, design (research + brainstorm → spec → build), "
             "research (autonomous research optimization), review (on-demand PR review), "
             "qa (QA verification pipeline for PRs), "
             "or create (meta-mode for creating new factory modes)",
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
    p.add_argument("--runner", default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--refine", default=None, metavar="REQUEST",
        help="Refinement mode: classify and implement a user-directed change. "
             "Mutually exclusive with --mode design, --mode research, --mode meta, --prompt, --focus",
    )
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into agent prompts")
    clean_pr_group = p.add_mutually_exclusive_group()
    clean_pr_group.add_argument("--clean-pr", action="store_true", default=None, dest="clean_pr",
                                help="Enable clean PR mode: strip non-essential artifacts before PR")
    clean_pr_group.add_argument("--no-clean-pr", action="store_false", dest="clean_pr",
                                help="Disable clean PR mode")
    p.add_argument("--tmux-persist", action="store_true", default=False,
                    help="Run agent interactively in a tmux window instead of headless (claude only)")
    p.add_argument("--bg", action="store_true", default=False,
                    help="Dispatch agent as a background session via claude agent view (claude only)")
    p.add_argument("--bg-agents", action="store_true", default=False,
                    help="Background sub-agents (via FACTORY_BG=1) while CEO runs in foreground")
    p.add_argument("--pr", type=int, default=None,
                    help="PR number for --mode review or --mode qa (required when mode=review or mode=qa)")
    p.add_argument("--repo", default=None,
                    help="Repository (owner/repo) for --mode review or --mode qa (optional, defaults to current repo)")
    p.add_argument("--run-id", default=None, dest="run_id",
                    help="Use a specific run ID (e.g., UUID from external orchestrator). "
                         "First 8 chars are used for worktree naming")

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
        choices=RUN_MODES,
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
    p.add_argument("--runner", default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into agent prompts")
    run_clean_pr_group = p.add_mutually_exclusive_group()
    run_clean_pr_group.add_argument("--clean-pr", action="store_true", default=None, dest="clean_pr",
                                    help="Enable clean PR mode: strip non-essential artifacts before PR")
    run_clean_pr_group.add_argument("--no-clean-pr", action="store_false", dest="clean_pr",
                                    help="Disable clean PR mode")
    p.add_argument("--tmux-persist", action="store_true", default=False,
                    help="Run agent interactively in a tmux window instead of headless (claude only)")
    p.add_argument("--bg", action="store_true", default=False,
                    help="Dispatch agent as a background session via claude agent view (claude only)")
    p.add_argument("--bg-agents", action="store_true", default=False,
                    help="Background sub-agents (via FACTORY_BG=1) while CEO runs in foreground")
    p.add_argument("--run-id", default=None, dest="run_id",
                    help="Use a specific run ID (e.g., UUID from external orchestrator). "
                         "First 8 chars are used for worktree naming")

    # tmux — launch factory run in a detached tmux session
    p = sub.add_parser("tmux", help="Launch factory run in a detached tmux session")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--session", default=None, help="Custom tmux session name")
    p.add_argument(
        "--mode",
        choices=CEO_MODES,
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
    p.add_argument("--runner", default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--focus", default=None,
        help="Target a specific item: backlog name, issue number, URL, or shorthand",
    )
    p.add_argument(
        "--refine", default=None, metavar="REQUEST",
        help="Refinement mode: classify and implement a user-directed change",
    )
    tmux_clean_pr = p.add_mutually_exclusive_group()
    tmux_clean_pr.add_argument("--clean-pr", action="store_true", default=None, dest="clean_pr",
                                help="Enable clean PR mode")
    tmux_clean_pr.add_argument("--no-clean-pr", action="store_false", dest="clean_pr",
                                help="Disable clean PR mode")
    p.add_argument(
        "--prompt", default=None,
        help="Path to a prompt/spec file",
    )
    p.add_argument("--branch", default=None,
                    help="Target branch for PRs")
    p.add_argument("--min-growth", type=int, default=None,
                    help="Minimum guaranteed growth hypotheses")
    p.add_argument("--max-new", type=int, default=None,
                    help="Max new items added to backlog per cycle")
    p.add_argument("--discover-only", action="store_true", default=False,
                    help="Only run discovery and review — do not chain into improve")
    p.add_argument("--bg-agents", action="store_true", default=False,
                    help="Background sub-agents (via FACTORY_BG=1) while CEO runs in foreground")
    p.add_argument("--tmux-persist", action="store_true", default=False,
                    help="Run agent interactively in a tmux window instead of headless (claude only)")
    p.add_argument("--use-profile", action="store_true", default=False,
                    help="Inject user profile (~/.factory/profile.md) into agent prompts")

    # tmux-ls — list factory tmux sessions
    p = sub.add_parser("tmux-ls", help="List running factory tmux sessions")
    p.add_argument("--json", action="store_true", default=False, dest="json_output",
                    help="Output as JSON array for programmatic consumption")

    # tmux-capture — capture output from a factory tmux session
    p = sub.add_parser("tmux-capture", help="Capture recent output from a factory tmux session")
    p.add_argument("path", nargs="?", default=None, help="Project path (derives session name)")
    p.add_argument("--session", default=None, help="Session name to capture from")
    p.add_argument("--lines", type=int, default=-100, help="Number of lines to capture (default: -100)")

    # tmux-stop — stop factory tmux sessions
    p = sub.add_parser("tmux-stop", help="Stop factory tmux session(s)")
    p.add_argument("--session", default=None, help="Session name to stop")
    p.add_argument("--path", default=None, help="Project path (derives session name)")
    p.add_argument("--all", action="store_true", default=False, dest="stop_all",
                    help="Stop ALL factory tmux sessions (required when no --session/--path given)")
    p.add_argument("--force", action="store_true", default=False,
                    help="Force-kill a session even if it's not in the factory registry")

    # refactory — persistent supervisor agent
    p = sub.add_parser("refactory", help="Launch the re:factory persistent supervisor agent")
    p.add_argument("path", nargs="?", default=None,
                    help="Project directory (default: current working directory)")
    p.add_argument("--reset", action="store_true", default=False,
                    help="Reset session (new session ID, fresh start)")
    p.add_argument("--model", default=None,
                    help="Claude model override")

    # workflow — graph engine commands
    from factory.workflow.cli import add_workflow_parser
    add_workflow_parser(sub)  # type: ignore[arg-type]

    return parser


def _load_env_local() -> None:
    """Auto-load .env.local if present, exporting vars into os.environ."""
    for candidate in [Path(".env.local"), Path.home() / "remote-factory" / ".env.local"]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
            break


def main(argv: list[str] | None = None) -> int:
    _load_env_local()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        if sys.stdin.isatty() and sys.stderr.isatty():
            return cmd_refactory(args)
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
        "clean-pr": cmd_clean_pr,
        "baseline": cmd_baseline,
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
        "usage": cmd_usage,
        "runners": cmd_runners_list,
        "agent": cmd_agent,
        "ceo": cmd_ceo,
        "run": cmd_run,
        "tmux": cmd_tmux,
        "tmux-ls": cmd_tmux_ls,
        "tmux-capture": cmd_tmux_capture,
        "tmux-stop": cmd_tmux_stop,
        "refactory": cmd_refactory,
        "workflow": lambda a: __import__("factory.workflow.cli", fromlist=["cmd_workflow"]).cmd_workflow(a),
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
