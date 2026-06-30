"""CLI ceo commands."""
from __future__ import annotations

import argparse
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

from factory.cli._helpers import _WIZARD_INPUT_PATH, _emit_cli_event, _ensure_dashboard, _print_banner, _read_target_branch, _run, _safe_is_dir, _safe_is_file, _show_spinner

if TYPE_CHECKING:
    from factory.messages import Message

log = structlog.get_logger()

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
    from factory.cli import build_parser
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
        handler = cmd_ceo if ns.command == "ceo" else cmd_study
        if handler:
            return handler(ns)

    print(f"  Error: unexpected command type: {ns.command}", file=sys.stderr)
    return 1


# ── subcommand handlers ────────────────────────────────────────


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

