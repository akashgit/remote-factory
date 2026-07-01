"""Welcome wizard — interactive classification and dispatch."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import threading
from pathlib import Path

import structlog

from factory.cli._helpers import _WIZARD_INPUT_PATH, _print_banner, _run, _safe_is_dir, _safe_is_file, _show_spinner

log = structlog.get_logger()


def _quick_classify(user_input: str) -> list[dict[str, str]] | None:
    """Deterministic fast path for paths, files, and URLs. Returns None if LLM needed."""
    from factory.cli.ceo import _is_github_url

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

        first_brace = text.find("{")
        first_bracket = text.find("[")

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
            if raw.isdigit():
                answers[key] = raw
            else:
                answers[key] = json.dumps(raw)

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
    """Substitute ``{key}`` placeholders in suggestion commands."""
    result: list[dict[str, str]] = []
    placeholder_re = re.compile(r"\{(\w+)\}")

    for s in suggestions:
        cmd = s.get("command", "")
        for key, value in answers.items():
            cmd = cmd.replace(f"{{{key}}}", value)
        remaining = placeholder_re.findall(cmd)
        if remaining:
            continue
        result.append({**s, "command": cmd})

    return result


def _welcome_wizard() -> int:
    """Interactive welcome: banner -> input -> classify -> present -> dispatch."""
    import factory.cli.ceo as _ceo

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
        and not _ceo._is_github_url(user_input)
    ):
        wizard_file = _WIZARD_INPUT_PATH.expanduser()
        wizard_file.parent.mkdir(parents=True, exist_ok=True)
        wizard_file.write_text(user_input)
        log.info("wizard.long_input_redirect", file=str(wizard_file), length=len(user_input))
        user_input = str(wizard_file)

    # -- classification ---------------------------------------------------
    follow_ups: list[dict[str, object]] = []
    suggestions: list[dict[str, str]] | None = _ceo._quick_classify(user_input)

    if suggestions is None:
        llm_result = _ceo._classify_with_llm(user_input)
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
            return 0
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

    from factory.cli._main import build_parser
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
        from factory.cli.admin import cmd_study

        handler = _ceo.cmd_ceo if ns.command == "ceo" else cmd_study
        if handler is not None:
            return handler(ns)

    print(f"  Error: unexpected command type: {ns.command}", file=sys.stderr)
    return 1
