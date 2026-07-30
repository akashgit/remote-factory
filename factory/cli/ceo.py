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
    handle_deep_qa_mode,
    handle_review_mode,
)
from factory.cli._path_resolver import _resolve_focus_issues


# ── subcommand handlers ──────────────────────────────────────


def cmd_ceo(args: argparse.Namespace) -> int:
    """Launch the Factory CEO agent to orchestrate a project."""
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    raw_path: str | None = getattr(args, "path", None)

    validated = _validate_ceo_flags(args)
    if isinstance(validated, int):
        return validated
    mode, headless, bg, bg_agents, prompt_file, focus, dir_name, refine_request, auto_approve, from_plan = validated

    assert raw_path is not None

    if mode == "review":
        return handle_review_mode(args, raw_path, headless)
    if mode == "deep-qa":
        return handle_deep_qa_mode(args, raw_path, headless)

    resolved = _resolve_ceo_project(raw_path, mode, headless, bg, focus, dir_name, prompt_file)
    if isinstance(resolved, int):
        return resolved
    (project_path, context, design_idea, research_ideation,
     deferred_spec, needs_materialize, design_existing, create_description,
     update_existing_mode) = resolved

    no_github = getattr(args, "no_github", False)
    if no_github:
        os.environ["FACTORY_NO_GITHUB"] = "1"
    refine_request = getattr(args, "refine", None)

    if refine_request:
        if mode and mode != "auto":
            print(f"Error: --refine and --mode {mode} are mutually exclusive.", file=sys.stderr)
            return 1
        if prompt_file:
            print("Error: --refine and --prompt are mutually exclusive.", file=sys.stderr)
            return 1
        if focus:
            print("Error: --refine and --focus are mutually exclusive.", file=sys.stderr)
            return 1
        if not Path(raw_path).expanduser().resolve().is_dir():
            print(
                "Error: --refine requires an existing project directory, not a URL or idea.",
                file=sys.stderr,
            )
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
            print(
                f"Error: project path must be an existing directory for review mode: {raw_path}",
                file=sys.stderr,
            )
            return 1

        _print_banner("review")

        # Ensure workflow skills are generated before resolving prompt
        from factory.skill_cache import ensure_skills
        ensure_skills(project_path)

        # Verify the required workflow skill exists
        review_skill = project_path / "skills" / "workflow-review" / "SKILL.md"
        if not review_skill.exists():
            print(
                f"Error: workflow skill not found: {review_skill}\n"
                f"This usually indicates a failure in skill generation.",
                file=sys.stderr,
            )
            return 1

        repo_flag = f" --repo {repo}" if repo else ""
        repo_clause = f" in repo `{repo}`" if repo else ""
        task = (
            f"Project: {project_path}\nMode: review\n\n"
            f"## PR Review Directive\n\n"
            f"Review PR #{pr_number}{repo_clause}.\n\n"
            f"This is a review-only run — no experiment lifecycle, no Builder iterations.\n\n"
            f"Execute these steps:\n"
            f"1. Run baseline eval (factory eval) to get $SCORE_BEFORE\n"
            f"2. Run the deep-QA pipeline (health_checker, code_reviewer, adversarial_tester) — "
            f"single pass, iteration 1/1, no Builder fix loop\n"
            f"3. Run Hard Precheck Gate\n"
            f"4. Post verdict via "
            f"factory review --verdict <KEEP|REVERT> --pr {pr_number} "
            f'--reason "$REASON" '
            f"--qa-body-file .factory/reviews/adversarial-qa.md"
            f"{repo_flag}\n"
            f"\nSet $REASON to the QA verdict summary (e.g. 'QA: CLEAN — 2854 tests pass, 0 issues' "
            f"or 'QA: ISSUES_FOUND — 3 critical issues'). Set $VERDICT to KEEP if QA is CLEAN, REVERT otherwise.\n"
        )

        if not headless:
            from factory.models import AgentRunRequest

            prompt = resolve_prompt("ceo", project_path)
            runner = get_runner(runner_name)
            return runner.interactive_run(
                AgentRunRequest(
                    prompt=prompt,
                    task=task,
                    cwd=project_path,
                    model=model,
                    role="ceo",
                    skip_permissions=True,
                )
            )

        from factory.ceo_completion import run_ceo_with_completion_guard

        result, code = _run(
            run_ceo_with_completion_guard(
                project_path,
                task,
                mode="review",
                runner_name=runner_name,
                model=model,
                timeout=7200.0,
                max_respawns=1,
            )
        )
        print(result)
        return code


    # ── deep-qa mode early exit ───────────────────────────────
    if mode == "deep-qa":
        pr_number = getattr(args, "pr", None)
        if pr_number is None:
            print("Error: --mode deep-qa requires --pr <number>", file=sys.stderr)
            return 1

        repo = getattr(args, "repo", None)
        model = _resolve_model(args)
        runner_name = _resolve_runner(args)

        project_path = Path(raw_path).expanduser().resolve()
        if not project_path.is_dir():
            print(
                f"Error: project path must be an existing directory for deep-qa mode: {raw_path}",
                file=sys.stderr,
            )
            return 1

        _print_banner("deep-qa")

        # Ensure workflow skills are generated before resolving prompt
        from factory.skill_cache import ensure_skills
        ensure_skills(project_path)

        # Verify the required workflow skill exists
        deep_qa_skill = project_path / "skills" / "workflow-deep-qa" / "SKILL.md"
        if not deep_qa_skill.exists():
            print(
                f"Error: workflow skill not found: {deep_qa_skill}\n"
                f"This usually indicates a failure in skill generation.",
                file=sys.stderr,
            )
            return 1

        repo_flag = f" --repo {repo}" if repo else ""
        repo_clause = f" in repo `{repo}`" if repo else ""
        task = (
            f"Project: {project_path}\nMode: deep-qa\n\n"
            f"## Deep-QA Verification Directive\n\n"
            f"Run the deep-QA verification pipeline for PR #{pr_number}{repo_clause}.\n\n"
            f"Execute the 3-specialist pipeline:\n"
            f"1. health_checker — run eval, compare scores, write health-check.md\n"
            f"2. code_reviewer — 7-category code review, write code-review.md\n"
            f"3. adversarial_tester — skeptical feature testing, write adversarial-qa.md\n\n"
            f"Key parameters:\n"
            f"- PR_NUMBER={pr_number}\n"
            f"- PROJECT_PATH={project_path}\n"
            f"{f'- REPO={repo}' + chr(10) if repo else ''}"
            f"\nPost the final verdict via:\n"
            f"factory review --verdict <KEEP|REVERT> --pr {pr_number} "
            f'--reason "$REASON" '
            f"--qa-body-file .factory/reviews/adversarial-qa.md"
            f"{repo_flag}\n"
            f"\nSet $REASON to the QA verdict summary (e.g. 'QA: CLEAN — 2854 tests pass, 0 issues' "
            f"or 'QA: ISSUES_FOUND — 3 critical issues'). Set $VERDICT to KEEP if QA is CLEAN, REVERT otherwise.\n"
            f"\nIMPORTANT: Do NOT post any PR comments (gh pr comment, gh issue comment). "
            f"The factory review command above is the ONLY GitHub output artifact.\n"
        )

        from factory.agents.runner import begin_cycle_session, complete_cycle_session

        cycle_span_id = begin_cycle_session(project_path, cycle_id="deep-qa", model=model)

        if not headless:
            from factory.models import AgentRunRequest

            prompt = resolve_prompt("ceo", project_path)
            runner = get_runner(runner_name)
            rc = runner.interactive_run(
                AgentRunRequest(
                    prompt=prompt,
                    task=task,
                    cwd=project_path,
                    model=model,
                    role="ceo",
                    skip_permissions=True,
                )
            )
            complete_cycle_session(project_path, cycle_span_id)
            return rc

        from factory.ceo_completion import run_ceo_with_completion_guard

        result, code = _run(
            run_ceo_with_completion_guard(
                project_path,
                task,
                mode="deep-qa",
                runner_name=runner_name,
                model=model,
                timeout=7200.0,
                max_respawns=1,
            )
        )
        complete_cycle_session(project_path, cycle_span_id)
        print(result)
        return code

    _design_is_existing = (
        mode == "design" and raw_path and _safe_is_dir(Path(raw_path).expanduser().resolve())
    )

    if mode == "design":
        if headless:
            flag = "--bg" if bg else "--headless"
            print(
                f"Error: --mode design requires foreground mode (incompatible with {flag})",
                file=sys.stderr,
            )
            return 1
        if prompt_file:
            print(
                "Error: --mode design and --prompt are mutually exclusive. "
                "Design mode generates the spec; --prompt provides one.",
                file=sys.stderr,
            )
            return 1
        if focus and not _design_is_existing:
            print(
                "Error: --mode design and --focus are mutually exclusive "
                "for new ideas. To discuss a topic on an existing project, "
                'pass the project path: factory ceo /path --mode design --focus "topic"',
                file=sys.stderr,
            )
            return 1

    if mode == "create":
        if headless:
            flag = "--bg" if bg else "--headless"
            print(
                f"Error: --mode create requires foreground mode (incompatible with {flag})",
                file=sys.stderr,
            )
            return 1
        if prompt_file:
            print(
                "Error: --mode create and --prompt are mutually exclusive. "
                "Create mode generates the workflow from a description.",
                file=sys.stderr,
            )
            return 1
    if mode == "research":
        if prompt_file:
            print(
                "Error: --mode research and --prompt are mutually exclusive. "
                "Research ideation generates the spec; --prompt provides one.",
                file=sys.stderr,
            )
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
            print(
                "Error: --mode create requires an existing project directory. "
                "Pass the factory project path: factory ceo /path/to/factory --mode create",
                file=sys.stderr,
            )
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
            slug = (
                _slugify(dir_name)
                if dir_name
                else _slugify(resolved_file.stem.split("—")[0].strip())
            )
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
    elif (
        mode == "research"
        and not _safe_is_dir(resolved := Path(raw_path).expanduser())
        and not _safe_is_file(resolved)
    ):
        # New research project from idea — enter research ideation
        if headless:
            flag = "--bg" if bg else "--headless"
            print(
                "Error: --mode research for new projects requires foreground mode "
                f"(incompatible with {flag})",
                file=sys.stderr,
            )
            return 1
        if focus:
            print(
                "Error: --focus cannot be used with research ideation for new projects. "
                "--focus targets existing backlog items.",
                file=sys.stderr,
            )
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
    issue_numbers: list[int] = []
    issue_urls: list[str] = []
    if focus:
        from factory.issue import has_multi_issue_refs

        if has_multi_issue_refs(focus) and no_github:
            print(
                "Error: --focus resolved to an issue reference, but --no-github is set. "
                "Issue fetching requires GitHub/GitLab CLI access.",
                file=sys.stderr,
            )
            return 1
        multi_resolved = _resolve_focus_issues(focus, project_path)
        if multi_resolved:
            if len(multi_resolved) == 1:
                title, context, issue_number, issue_url = multi_resolved[0]
                focus = f"{title} (issue #{issue_number})"
            else:
                parts = []
                for title, ctx, num, url in multi_resolved:
                    parts.append(f"{title} (issue #{num})")
                    issue_numbers.append(num)
                    issue_urls.append(url)
                focus = " + ".join(parts)
                context = None

    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path,
            has_prompt=bool(prompt_file or context),
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
        update_existing_mode=update_existing_mode,
        deferred_spec=deferred_spec,
        needs_materialize=needs_materialize,
        refine_request=refine_request,
        issue_number=issue_number,
        issue_url=issue_url,
        issue_numbers=issue_numbers,
        issue_urls=issue_urls,
        no_github=no_github,
        raw_path=raw_path,
        from_plan=from_plan,
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

    loop = getattr(args, "loop", False)
    if loop:
        tune_skill_src = Path(__file__).parent.parent / "agents" / "skills" / "workflow-tune.md"
        if tune_skill_src.is_file():
            commands_dir = project_path / ".claude" / "commands"
            commands_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tune_skill_src, commands_dir / "workflow-tune.md")

    reset = getattr(args, "reset", False)
    session_file = project_path / ".refactory" / "session.json"
    is_new_session = reset or not session_file.exists()
    session_id = get_session_id(project_path, reset=reset)
    model = getattr(args, "model", None)

    prompt = resolve_prompt("refactory")
    prompt_tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        prefix="refactory-prompt-",
        delete=False,
    )
    prompt_tmp.write(prompt)
    prompt_tmp.close()

    if is_new_session:
        cmd = [
            "claude",
            "--session-id",
            session_id,
            "--append-system-prompt-file",
            prompt_tmp.name,
            "--disallowedTools",
            "Agent",
            "--dangerously-skip-permissions",
        ]
    else:
        cmd = [
            "claude",
            "--resume",
            session_id,
            "--append-system-prompt-file",
            prompt_tmp.name,
            "--disallowedTools",
            "Agent",
            "--dangerously-skip-permissions",
        ]

    if model:
        cmd.extend(["--model", model])

    os.chdir(project_path)
    os.execvp("claude", cmd)
    return 0
