"""Build the CEO agent task string from mode and optional context."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.messages import Message


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
