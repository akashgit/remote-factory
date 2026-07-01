"""CLI ceo commands — thin dispatcher delegating to extracted modules."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from factory.cli._ceo_helpers import (
    _execute_ceo,
    _resolve_ceo_project,
    _validate_ceo_flags,
    _validate_late_flags,
)
from factory.cli._mode_handlers import (
    _auto_detect_mode,
    handle_qa_mode,
    handle_review_mode,
)
from factory.cli._path_resolver import _resolve_focus_issue


# ── subcommand handlers ──────────────────────────────────────


def cmd_ceo(args: argparse.Namespace) -> int:
    """Launch the Factory CEO agent to orchestrate a project."""
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    raw_path = getattr(args, "path", None)

    validated = _validate_ceo_flags(args)
    if isinstance(validated, int):
        return validated
    mode, headless, bg, bg_agents, prompt_file, focus, dir_name, refine_request = validated

    if mode == "review":
        return handle_review_mode(args, raw_path, headless)
    if mode == "qa":
        return handle_qa_mode(args, raw_path, headless)

    resolved = _resolve_ceo_project(raw_path, mode, headless, bg, focus, dir_name, prompt_file)
    if isinstance(resolved, int):
        return resolved
    (project_path, context, design_idea, research_ideation,
     deferred_spec, needs_materialize, design_existing, create_description) = resolved

    no_github = getattr(args, "no_github", False)
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

    err = _validate_late_flags(
        mode, focus, prompt_file, research_ideation,
        design_existing, project_path, no_github, issue_number,
    )
    if err is not None:
        return err

    if design_existing:
        banner_mode = "design"
    elif mode in ("design", "research") and (design_idea or research_ideation):
        banner_mode = "ideation"
    else:
        banner_mode = mode

    return _execute_ceo(
        args=args,
        project_path=project_path,
        context=context,
        mode=mode,
        banner_mode=banner_mode,
        headless=headless,
        bg=bg,
        bg_agents=bg_agents,
        focus=focus,
        prompt_file=prompt_file,
        design_idea=design_idea,
        design_existing=design_existing,
        research_ideation=research_ideation,
        create_description=create_description,
        deferred_spec=deferred_spec,
        needs_materialize=needs_materialize,
        refine_request=refine_request,
        issue_number=issue_number,
        issue_url=issue_url,
        no_github=no_github,
        raw_path=raw_path,
    )


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
    return 0
