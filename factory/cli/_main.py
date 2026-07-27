"""CLI parser construction and main dispatch."""

from __future__ import annotations

import argparse
import sys

from factory.cli._helpers import CEO_MODES, RUN_MODES, _load_env_local


_REFACTORY_AGENT_COMMANDS: frozenset[str] = frozenset(
    {
        "ceo",
        "run",
        "tmux",
        "tmux-ls",
        "tmux-stop",
        "tmux-capture",
        "discover",
        "init",
        "detect",
        "eval",
        "history",
        "study",
        "status",
        "backlog-list",
        "backlog-add",
        "checkpoint",
        "resume",
        "ace",
        "ace-stats",
    }
)


_COMMAND_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Entry Points",
        [
            "ceo",
            "run",
            "tmux",
            "tmux-ls",
            "tmux-capture",
            "tmux-stop",
            "refactory",
            "dashboard",
            "agent",
        ],
    ),
    ("Project Setup", ["home", "detect", "discover", "init"]),
    (
        "Experiment Lifecycle",
        [
            "begin",
            "finalize",
            "guard",
            "precheck",
            "log",
            "emit",
            "review",
        ],
    ),
    (
        "Project Intelligence",
        [
            "eval",
            "history",
            "study",
            "status",
            "summary",
            "diff",
            "explain",
            "export",
            "research",
            "insights",
            "report-update",
            "baseline",
            "clean-pr",
            "spec",
            "adversarial-state",
        ],
    ),
    (
        "Backlog & Refinement",
        [
            "backlog-add",
            "backlog-list",
            "backlog-remove",
            "deferred-list",
            "deferred-remove",
            "refine-status",
            "refine-begin",
            "refine-complete",
            "message",
        ],
    ),
    (
        "Knowledge & Archive",
        [
            "archive",
            "vault-init",
            "backfill-citations",
            "backfill-archive",
        ],
    ),
    ("Self-Evolution", ["ace", "ace-stats", "digest", "workflow"]),
    (
        "Configuration",
        [
            "config",
            "profile",
            "install",
            "self-update",
            "runners",
            "usage",
            "serve-mcp",
        ],
    ),
    (
        "Validation & Recovery",
        [
            "leakage-check",
            "validate-research",
            "checkpoint",
            "resume",
            "notify",
            "registry-list",
        ],
    ),
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

        refactory_filter = "--refactory-agent" in sys.argv

        grouped_cmds: set[str] = set()
        for group_name, cmds in _COMMAND_GROUPS:
            lines = []
            for cmd in cmds:
                if cmd in sub_action._name_parser_map and cmd in help_map:
                    if refactory_filter and cmd not in _REFACTORY_AGENT_COMMANDS:
                        continue
                    lines.append(f"  {cmd:25s}{help_map[cmd]}")
                    grouped_cmds.add(cmd)
            if lines:
                parts.append(f"\n{group_name}:\n" + "\n".join(lines))

        if not refactory_filter:
            ungrouped = [
                c for c in help_map if c not in grouped_cmds and c in sub_action._name_parser_map
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
    parser.add_argument(
        "--refactory-agent",
        action="store_true",
        help="Show only commands used by the re:factory agent",
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
    p.add_argument(
        "--skip-project-eval",
        action="store_true",
        default=False,
        help="Skip user-defined project eval dimensions (run only hygiene + growth)",
    )

    # guard
    p = sub.add_parser("guard", help="Check guard rules, print violations or 'clean'")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--baseline", required=True, help="Baseline commit SHA")
    p.add_argument("--check-scope", action="store_true", help="Also check file scope")
    p.add_argument(
        "--check-surfaces",
        action="store_true",
        help="Also check fixed surface constraints (research mode)",
    )

    # begin
    p = sub.add_parser("begin", help="Start experiment, print ID")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--hypothesis", required=True, help="Experiment hypothesis text")

    # finalize
    p = sub.add_parser("finalize", help="Finalize experiment with verdict")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--id", required=True, type=int, help="Experiment ID")
    p.add_argument(
        "--verdict", required=True, choices=["keep", "revert", "error"], help="Experiment verdict"
    )
    p.add_argument("--hypothesis", default=None, help="Hypothesis text")
    p.add_argument("--summary", default=None, help="Change summary")
    p.add_argument("--cost", default=None, type=float, help="Cost in USD")
    p.add_argument("--issue", default=None, type=int, help="GitHub issue number")
    p.add_argument("--pr", default=None, type=int, help="GitHub PR number")
    p.add_argument("--notes", default=None, help="Additional notes")
    p.add_argument("--score-before", type=float, default=None, help="Eval score before change")
    p.add_argument("--score-after", type=float, default=None, help="Eval score after change")
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Bypass precheck gate (for pre-existing failures)",
    )

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
        "--projects-dir",
        default=None,
        help="Directory containing factory-managed projects for cross-project insights",
    )
    p.add_argument(
        "--focus",
        default=None,
        help="Targeted mode: filter observations to a single backlog item",
    )

    # backlog-remove (alias: deferred-remove)
    p = sub.add_parser(
        "backlog-remove", aliases=["deferred-remove"], help="Remove a completed backlog item"
    )
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
    p = sub.add_parser(
        "leakage-check", help="Scan text for ground truth leakage against fixed surfaces"
    )
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--text", default=None, help="Text to scan for leakage (hypothesis, strategy, etc.)"
    )
    p.add_argument(
        "--text-file",
        default=None,
        help="Path to file containing text to scan (safer for large diffs)",
    )
    p.add_argument(
        "--sensitivity",
        choices=["low", "medium", "high"],
        default="medium",
        help="Sensitivity level (default: medium)",
    )

    # validate-research
    p = sub.add_parser(
        "validate-research", help="Validate research mode configuration for ground truth isolation"
    )
    p.add_argument("path", help="Path to the project")

    # adversarial-state
    p = sub.add_parser("adversarial-state", help="Inspect or reset adversarial eval loop state")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--reset", action="store_true", default=False, help="Reset adversarial state to defaults"
    )

    # backfill-citations
    p = sub.add_parser(
        "backfill-citations", help="Extract citations from experiment text into citations.json"
    )
    p.add_argument("path", help="Path to the project")

    # backfill-archive
    p = sub.add_parser(
        "backfill-archive", help="Generate archive notes for experiments missing from archive"
    )
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
        "--projects-dir",
        default=None,
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
        "--projects-dir",
        default=None,
        help="Directory containing factory-managed projects (default: from registry or ~/factory-projects)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
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
    p.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.6,
        help="Similarity threshold for anti-pattern detection (default: 0.6)",
    )

    # clean-pr
    p = sub.add_parser("clean-pr", help="Strip non-essential artifacts from a PR diff")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--exp", type=int, default=None, help="Experiment ID (archives full diff before stripping)"
    )

    # baseline
    p = sub.add_parser("baseline", help="Fetch stored eval baseline from eval-data branch")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--commit",
        default=None,
        help="Commit SHA to look up (default: git merge-base HEAD <target-branch>)",
    )

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
    p.add_argument(
        "--verdict",
        required=True,
        choices=["keep", "revert", "error", "tier3_exit"],
        help="Refinement verdict",
    )

    # review
    p = sub.add_parser("review", help="Format and post a structured review on a GitHub PR")
    p.add_argument(
        "--verdict",
        required=True,
        choices=["keep", "revert", "KEEP", "REVERT"],
        help="Review verdict",
    )
    p.add_argument("--reason", default=None, help="One-sentence reason for the verdict")
    p.add_argument("--score-before", type=float, default=None, help="Score before change")
    p.add_argument("--score-after", type=float, default=None, help="Score after change")
    p.add_argument("--threshold", type=float, default=0.8, help="Eval threshold")
    p.add_argument("--guards", default=None, help="Guard results as 'check:PASS,check:FAIL' pairs")
    p.add_argument("--precheck-summary", default=None, help="Precheck gate output summary")
    p.add_argument("--code-notes", default=None, help="Code review notes separated by | (pipe)")
    p.add_argument("--experiment-id", type=int, default=None, help="Experiment ID")
    p.add_argument("--hypothesis", default=None, help="Experiment hypothesis text")
    p.add_argument("--pr", type=int, default=None, help="PR number to post review on")
    p.add_argument("--repo", default=None, help="GitHub repo (owner/name) for the PR")
    p.add_argument(
        "--qa-body-file",
        default=None,
        help="Path to file containing QA analysis to include in review",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=False, help="Print review without posting"
    )

    # checkpoint
    p = sub.add_parser(
        "checkpoint", help="Show or save a CEO checkpoint for crash-resilient resume"
    )
    p.add_argument("path", help="Path to the project")
    ckpt_action = p.add_mutually_exclusive_group()
    ckpt_action.add_argument("--save", action="store_true", default=False, help="Save a checkpoint")
    ckpt_action.add_argument(
        "--clear", action="store_true", default=False, help="Clear the checkpoint file"
    )
    p.add_argument("--mode", default=None, help="CEO mode (e.g. improve, build)")
    p.add_argument("--experiment", type=int, default=None, help="Active experiment ID")
    p.add_argument(
        "--completed", default=None, help="Comma-separated list of completed agent roles"
    )
    p.add_argument("--pending", default=None, help="Comma-separated list of pending agent roles")
    p.add_argument(
        "--scores", default=None, help="JSON dict of eval scores (e.g. '{\"tests\": 0.9}')"
    )
    p.add_argument("--hypothesis", default=None, help="Current hypothesis text")
    p.add_argument(
        "--completed-hypotheses",
        default=None,
        help="Comma-separated list of completed experiment IDs (e.g. '1,2,3')",
    )

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
    p = sub.add_parser(
        "install",
        help="Install Factory agents as CLI agents (~/.claude/agents/ or ~/.codex/agents/)",
    )
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
    p.add_argument(
        "--json", action="store_true", default=False, help="Output as JSON instead of table"
    )

    # runners — runner management
    runners_parser = sub.add_parser("runners", help="Manage factory runners")
    runners_sub = runners_parser.add_subparsers(dest="runners_command")
    p_runners_list = runners_sub.add_parser("list", help="List all registered runners")
    p_runners_list.add_argument("--json", action="store_true", default=False, help="Output as JSON")

    # serve-mcp — MCP stdio server
    sub.add_parser("serve-mcp", help="Start the Factory MCP stdio server")

    # dashboard — live web dashboard
    p = sub.add_parser("dashboard", help="Launch the live Factory dashboard")
    p.add_argument(
        "--projects-dir",
        default="~/factory-projects",
        help="Directory containing factory-managed projects (default: ~/factory-projects)",
    )
    p.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    p.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")

    # config — user configuration management
    config_parser = sub.add_parser("config", help="Manage ~/.factory/config.toml")
    config_sub = config_parser.add_subparsers(dest="config_command")
    p_show = config_sub.add_parser("show", help="Show resolved config (secrets masked)")
    p_show.add_argument(
        "--reveal",
        action="store_true",
        default=False,
        help="Show full secret values instead of masking",
    )
    config_sub.add_parser("edit", help="Open config.toml in $EDITOR")
    config_sub.add_parser("migrate", help="Create starter config.toml from current env vars")

    # profile — user profile management
    profile_parser = sub.add_parser(
        "profile", help="Manage the user profile at ~/.factory/profile.md"
    )
    profile_sub = profile_parser.add_subparsers(dest="profile_command")
    p_build = profile_sub.add_parser("build", help="Collect evidence and synthesize user profile")
    p_build.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="Project paths to collect evidence from (default: all registered)",
    )
    p_build.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print collected evidence without running LLM synthesis",
    )
    p_build.add_argument("--runner", default=None, help="CLI backend to use for synthesis")
    profile_sub.add_parser("show", help="Print the current user profile")

    # emit — emit a structured event to .factory/events.jsonl
    p = sub.add_parser("emit", help="Emit a structured event to .factory/events.jsonl")
    p.add_argument("event_type", help="Event type (e.g. agent.started, agent.completed)")
    p.add_argument("--agent", default=None, help="Agent role name")
    p.add_argument("--project", default=".", help="Project path")
    p.add_argument("--data", default=None, help="JSON string of additional event data")

    # agent — invoke a specialist agent directly
    p = sub.add_parser("agent", help="Invoke a specialist agent with a task")
    p.add_argument(
        "role",
        choices=[
            "researcher",
            "strategist",
            "builder",
            "qa",
            "health_checker",
            "code_reviewer",
            "adversarial_tester",
            "archivist",
            "ceo",
            "failure_analyst",
            "refiner",
        ],
        help="Agent role to invoke",
    )
    p.add_argument("--task", required=True, help="Task description for the agent")
    p.add_argument("--project", required=True, help="Path to the project")
    p.add_argument("--timeout", type=float, default=600.0, help="Timeout in seconds (default: 600)")
    p.add_argument(
        "--model",
        default=None,
        help="Claude model for agent subprocess (default: FACTORY_MODEL env var, or claude CLI default)",
    )
    p.add_argument(
        "--runner",
        default=None,
        help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')",
    )
    p.add_argument("--profile", default=None, help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--use-profile",
        action="store_true",
        default=False,
        help="Inject user profile (~/.factory/profile.md) into the agent prompt",
    )
    p.add_argument(
        "--tmux-persist",
        action="store_true",
        default=False,
        help="Run agent interactively in a tmux window instead of headless (claude only)",
    )
    p.add_argument(
        "--bg",
        action="store_true",
        default=False,
        help="Dispatch agent as a background session via claude agent view (claude only)",
    )
    p.add_argument(
        "--review-tag",
        default=None,
        help="Tag for distinct review output files (writes <role>-<tag>-latest.md)",
    )
    p.add_argument(
        "--parent-session",
        default=None,
        help="Parent session ID for linking specialist sessions to a CEO cycle session",
    )

    # ceo — launch the Factory CEO agent directly
    p = sub.add_parser("ceo", help="Launch the Factory CEO agent (interactive by default)")
    p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Project path, GitHub URL, idea file path, or prompt. "
        "In design mode, pass a raw idea string",
    )
    p.add_argument(
        "--prompt",
        default=None,
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
        "optimize (analyze and tune factory mode effectiveness — requires --focus <mode_name>), "
        "or create (meta-mode for creating or updating factory modes — "
        "use --focus \"mode_name: change\" to update an existing mode)",
    )
    p.add_argument(
        "--focus",
        default=None,
        help="Target a specific item: backlog name ('dashboard UI'), issue number (42), "
        "URL (https://github.com/o/r/issues/42), or shorthand (owner/repo#42). "
        "Issue refs are auto-detected and fetched via gh/glab CLI",
    )
    p.add_argument(
        "--dir",
        default=None,
        help="Working directory name for the new project (overrides auto-derived name from prompt or idea file). "
        "Ignored when pointing at an existing directory or GitHub URL.",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run in pipe mode (non-interactive) instead of foreground",
    )
    p.add_argument(
        "--discover-only",
        action="store_true",
        default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--no-github",
        action="store_true",
        default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument(
        "--min-growth",
        type=int,
        default=None,
        help="Minimum guaranteed growth hypotheses (default: 2)",
    )
    p.add_argument(
        "--max-new",
        type=int,
        default=None,
        help="Max new items added to backlog per cycle (default: 2)",
    )
    p.add_argument(
        "--branch",
        default=None,
        help="Target branch for PRs (default: from factory.md, fallback: main)",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)",
    )
    p.add_argument(
        "--runner",
        default=None,
        help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')",
    )
    p.add_argument("--profile", default=None, help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--refine",
        default=None,
        metavar="REQUEST",
        help="Refinement mode: classify and implement a user-directed change. "
        "Mutually exclusive with --mode design, --mode research, --mode meta, --prompt, --focus",
    )
    p.add_argument(
        "--use-profile",
        action="store_true",
        default=False,
        help="Inject user profile (~/.factory/profile.md) into agent prompts",
    )
    clean_pr_group = p.add_mutually_exclusive_group()
    clean_pr_group.add_argument(
        "--clean-pr",
        action="store_true",
        default=None,
        dest="clean_pr",
        help="Enable clean PR mode: strip non-essential artifacts before PR",
    )
    clean_pr_group.add_argument(
        "--no-clean-pr", action="store_false", dest="clean_pr", help="Disable clean PR mode"
    )
    p.add_argument(
        "--tmux-persist",
        action="store_true",
        default=False,
        help="Run agent interactively in a tmux window instead of headless (claude only)",
    )
    p.add_argument(
        "--bg",
        action="store_true",
        default=False,
        help="Dispatch agent as a background session via claude agent view (claude only)",
    )
    p.add_argument(
        "--bg-agents",
        action="store_true",
        default=False,
        help="Background sub-agents (via FACTORY_BG=1) while CEO runs in foreground",
    )
    p.add_argument(
        "--pr",
        type=int,
        default=None,
        help="PR number for --mode review or --mode deep-qa (required when mode=review or mode=deep-qa)",
    )
    p.add_argument(
        "--repo",
        default=None,
        help="Repository (owner/repo) for --mode review or --mode deep-qa (optional, defaults to current repo)",
    )
    p.add_argument(
        "--run-id",
        default=None,
        dest="run_id",
        help="Use a specific run ID (e.g., UUID from external orchestrator). "
        "First 8 chars are used for worktree naming",
    )
    p.add_argument(
        "--no-worktree",
        action="store_true",
        default=False,
        dest="no_worktree",
        help="Run directly in the project directory without creating a worktree "
        "(useful for testing in-flight branch changes)",
    )

    # run
    p = sub.add_parser("run", help="Run factory cycle (delegates to CEO agent)")
    p.add_argument("path", help="Project path, GitHub URL, idea file path, or prompt")
    p.add_argument(
        "--prompt",
        default=None,
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
        "--focus",
        default=None,
        help="Target a specific item: backlog name ('dashboard UI'), issue number (42), "
        "URL (https://github.com/o/r/issues/42), or shorthand (owner/repo#42). "
        "Issue refs are auto-detected and fetched via gh/glab CLI",
    )
    p.add_argument(
        "--discover-only",
        action="store_true",
        default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--no-github",
        action="store_true",
        default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        default=False,
        help="Enable heartbeat mode: run continuously with sleep between cycles",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=1800,
        help="Seconds to sleep between cycles (default: 1800)",
    )
    p.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Maximum number of cycles (default: unlimited)",
    )
    p.add_argument(
        "--min-growth",
        type=int,
        default=None,
        help="Minimum guaranteed growth hypotheses (default: 2)",
    )
    p.add_argument(
        "--max-new",
        type=int,
        default=None,
        help="Max new items added to backlog per cycle (default: 2)",
    )
    p.add_argument(
        "--branch",
        default=None,
        help="Target branch for PRs (default: from factory.md, fallback: main)",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)",
    )
    p.add_argument(
        "--runner",
        default=None,
        help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')",
    )
    p.add_argument("--profile", default=None, help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--use-profile",
        action="store_true",
        default=False,
        help="Inject user profile (~/.factory/profile.md) into agent prompts",
    )
    run_clean_pr_group = p.add_mutually_exclusive_group()
    run_clean_pr_group.add_argument(
        "--clean-pr",
        action="store_true",
        default=None,
        dest="clean_pr",
        help="Enable clean PR mode: strip non-essential artifacts before PR",
    )
    run_clean_pr_group.add_argument(
        "--no-clean-pr", action="store_false", dest="clean_pr", help="Disable clean PR mode"
    )
    p.add_argument(
        "--tmux-persist",
        action="store_true",
        default=False,
        help="Run agent interactively in a tmux window instead of headless (claude only)",
    )
    p.add_argument(
        "--bg",
        action="store_true",
        default=False,
        help="Dispatch agent as a background session via claude agent view (claude only)",
    )
    p.add_argument(
        "--bg-agents",
        action="store_true",
        default=False,
        help="Background sub-agents (via FACTORY_BG=1) while CEO runs in foreground",
    )
    p.add_argument(
        "--run-id",
        default=None,
        dest="run_id",
        help="Use a specific run ID (e.g., UUID from external orchestrator). "
        "First 8 chars are used for worktree naming",
    )
    p.add_argument(
        "--no-worktree",
        action="store_true",
        default=False,
        dest="no_worktree",
        help="Run directly in the project directory without creating a worktree "
        "(useful for testing in-flight branch changes)",
    )

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
    p.add_argument(
        "--attach", action="store_true", default=False, help="Attach to session after creating"
    )
    p.add_argument(
        "--no-github",
        action="store_true",
        default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)",
    )
    p.add_argument(
        "--runner",
        default=None,
        help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')",
    )
    p.add_argument("--profile", default=None, help="Credential profile from ~/.factory/config.toml")
    p.add_argument(
        "--focus",
        default=None,
        help="Target a specific item: backlog name, issue number, URL, or shorthand",
    )
    p.add_argument(
        "--refine",
        default=None,
        metavar="REQUEST",
        help="Refinement mode: classify and implement a user-directed change",
    )
    tmux_clean_pr = p.add_mutually_exclusive_group()
    tmux_clean_pr.add_argument(
        "--clean-pr",
        action="store_true",
        default=None,
        dest="clean_pr",
        help="Enable clean PR mode",
    )
    tmux_clean_pr.add_argument(
        "--no-clean-pr", action="store_false", dest="clean_pr", help="Disable clean PR mode"
    )
    p.add_argument(
        "--prompt",
        default=None,
        help="Path to a prompt/spec file",
    )
    p.add_argument("--branch", default=None, help="Target branch for PRs")
    p.add_argument(
        "--min-growth", type=int, default=None, help="Minimum guaranteed growth hypotheses"
    )
    p.add_argument(
        "--max-new", type=int, default=None, help="Max new items added to backlog per cycle"
    )
    p.add_argument(
        "--discover-only",
        action="store_true",
        default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--bg-agents",
        action="store_true",
        default=False,
        help="Background sub-agents (via FACTORY_BG=1) while CEO runs in foreground",
    )
    p.add_argument(
        "--tmux-persist",
        action="store_true",
        default=False,
        help="Run agent interactively in a tmux window instead of headless (claude only)",
    )
    p.add_argument(
        "--use-profile",
        action="store_true",
        default=False,
        help="Inject user profile (~/.factory/profile.md) into agent prompts",
    )

    # tmux-ls — list factory tmux sessions
    p = sub.add_parser("tmux-ls", help="List running factory tmux sessions")
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output as JSON array for programmatic consumption",
    )

    # tmux-capture — capture output from a factory tmux session
    p = sub.add_parser("tmux-capture", help="Capture recent output from a factory tmux session")
    p.add_argument("path", nargs="?", default=None, help="Project path (derives session name)")
    p.add_argument("--session", default=None, help="Session name to capture from")
    p.add_argument(
        "--lines", type=int, default=-100, help="Number of lines to capture (default: -100)"
    )

    # tmux-stop — stop factory tmux sessions
    p = sub.add_parser("tmux-stop", help="Stop factory tmux session(s)")
    p.add_argument("--session", default=None, help="Session name to stop")
    p.add_argument("--path", default=None, help="Project path (derives session name)")
    p.add_argument(
        "--all",
        action="store_true",
        default=False,
        dest="stop_all",
        help="Stop ALL factory tmux sessions (required when no --session/--path given)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force-kill a session even if it's not in the factory registry",
    )

    # spec — repo spec generation and analysis
    spec_parser = sub.add_parser("spec", help="Repo spec generation and analysis")
    spec_sub = spec_parser.add_subparsers(dest="spec_command")
    p_spec_gen = spec_sub.add_parser("generate", help="Generate a repo spec for a project")
    p_spec_gen.add_argument("path", help="Path to the project")
    p_spec_val = spec_sub.add_parser("validate", help="Validate a repo spec against the project")
    p_spec_val.add_argument("path", help="Path to the project")
    p_spec_scope = spec_sub.add_parser("scope", help="Scope a diff against the repo spec")
    p_spec_scope.add_argument("path", help="Path to the project")
    p_spec_scope.add_argument("--experiment", type=int, default=None, help="Experiment ID to scope")
    p_spec_update = spec_sub.add_parser("update", help="Update the repo spec from recent changes")
    p_spec_update.add_argument("path", help="Path to the project")
    p_spec_impact = spec_sub.add_parser("impact", help="Show impact subgraph for a module")
    p_spec_impact.add_argument("module", help="Module name to query")
    p_spec_impact.add_argument("--project", required=True, help="Path to the project")

    # refactory — persistent supervisor agent
    p = sub.add_parser("refactory", help="Launch the re:factory persistent supervisor agent")
    p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Project directory (default: current working directory)",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Reset session (new session ID, fresh start)",
    )
    p.add_argument("--model", default=None, help="Claude model override")

    # workflow — graph engine commands
    from factory.workflow.cli import add_workflow_parser

    add_workflow_parser(sub)  # type: ignore[arg-type]

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env_local()
    parser = build_parser()
    args = parser.parse_args(argv)

    import factory.cli as _cli

    if not args.command:
        if sys.stdin.isatty() and sys.stderr.isatty():
            return _cli.cmd_refactory(args)
        parser.print_help()
        return 1

    handlers = {
        "home": _cli.cmd_home,
        "detect": _cli.cmd_detect,
        "discover": _cli.cmd_discover,
        "init": _cli.cmd_init,
        "eval": _cli.cmd_eval,
        "guard": _cli.cmd_guard,
        "begin": _cli.cmd_begin,
        "finalize": _cli.cmd_finalize,
        "history": _cli.cmd_history,
        "notify": _cli.cmd_notify,
        "study": _cli.cmd_study,
        "backlog-remove": _cli.cmd_backlog_remove,
        "deferred-remove": _cli.cmd_backlog_remove,
        "backlog-list": _cli.cmd_backlog_list,
        "deferred-list": _cli.cmd_backlog_list,
        "backlog-add": _cli.cmd_backlog_add,
        "status": _cli.cmd_status,
        "summary": _cli.cmd_summary,
        "research": _cli.cmd_research,
        "backfill-citations": _cli.cmd_backfill_citations,
        "backfill-archive": _cli.cmd_backfill_archive,
        "diff": _cli.cmd_diff,
        "explain": _cli.cmd_explain,
        "export": _cli.cmd_export,
        "insights": _cli.cmd_insights,
        "report-update": _cli.cmd_report_update,
        "registry-list": _cli.cmd_registry_list,
        "ace": _cli.cmd_ace,
        "ace-stats": _cli.cmd_ace_stats,
        "digest": _cli.cmd_digest,
        "archive": _cli.cmd_archive,
        "precheck": _cli.cmd_precheck,
        "clean-pr": _cli.cmd_clean_pr,
        "baseline": _cli.cmd_baseline,
        "leakage-check": _cli.cmd_leakage_check,
        "validate-research": _cli.cmd_validate_research,
        "adversarial-state": _cli.cmd_adversarial_state,
        "refine-status": _cli.cmd_refine_status,
        "refine-begin": _cli.cmd_refine_begin,
        "refine-complete": _cli.cmd_refine_complete,
        "review": _cli.cmd_review,
        "checkpoint": _cli.cmd_checkpoint,
        "resume": _cli.cmd_resume,
        "log": _cli.cmd_log,
        "vault-init": _cli.cmd_vault_init,
        "message": _cli.cmd_message,
        "self-update": _cli.cmd_self_update,
        "install": _cli.cmd_install,
        "serve-mcp": _cli.cmd_serve_mcp,
        "dashboard": _cli.cmd_dashboard,
        "config": _cli.cmd_config,
        "profile": _cli.cmd_profile,
        "emit": _cli.cmd_emit,
        "usage": _cli.cmd_usage,
        "runners": _cli.cmd_runners_list,
        "agent": _cli.cmd_agent,
        "ceo": _cli.cmd_ceo,
        "run": _cli.cmd_run,
        "tmux": _cli.cmd_tmux,
        "tmux-ls": _cli.cmd_tmux_ls,
        "tmux-capture": _cli.cmd_tmux_capture,
        "tmux-stop": _cli.cmd_tmux_stop,
        "refactory": _cli.cmd_refactory,
        "spec": lambda a: {
            "generate": _cli.cmd_spec_generate,
            "validate": _cli.cmd_spec_validate,
            "scope": _cli.cmd_spec_scope,
            "update": _cli.cmd_spec_update,
            "impact": _cli.cmd_spec_impact,
        }.get(
            str(getattr(a, "spec_command", "")),
            lambda args: print("Usage: factory spec {generate,validate,scope,update,impact}") or 1,
        )(a),
        "workflow": lambda a: __import__(
            "factory.workflow.cli", fromlist=["cmd_workflow"]
        ).cmd_workflow(a),
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
