"""CLI parser construction and main dispatch."""

from __future__ import annotations

import argparse
import sys

from factory.cli._helpers import _load_env_local


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
    ("Self-Evolution", ["ace", "ace-stats", "digest", "workflow", "graph", "skillopt"]),
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
    from factory.cli._parser_groups import (
        add_archive_parsers,
        add_backlog_refinement_parsers,
        add_configuration_parsers,
        add_entry_point_parsers,
        add_experiment_lifecycle_parsers,
        add_project_intelligence_parsers,
        add_project_setup_parsers,
        add_self_evolution_parsers,
        add_validation_recovery_parsers,
    )

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

    add_project_setup_parsers(sub)
    add_experiment_lifecycle_parsers(sub)
    add_project_intelligence_parsers(sub)
    add_backlog_refinement_parsers(sub)
    add_archive_parsers(sub)
    add_self_evolution_parsers(sub)
    add_configuration_parsers(sub)
    add_validation_recovery_parsers(sub)
    add_entry_point_parsers(sub)

    # graph — code knowledge graph operations
    graph_parser = sub.add_parser("graph", help="Code knowledge graph via graphify")
    graph_sub = graph_parser.add_subparsers(dest="graph_command")
    p_graph_extract = graph_sub.add_parser("extract", help="Extract a code knowledge graph")
    p_graph_extract.add_argument("path", help="Path to the project")
    p_graph_update = graph_sub.add_parser("update", help="Incrementally update the knowledge graph")
    p_graph_update.add_argument("path", help="Path to the project")
    p_graph_status = graph_sub.add_parser("status", help="Show graph freshness and stats")
    p_graph_status.add_argument("path", help="Path to the project")

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
        "skillopt": _cli.cmd_skillopt,
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
            "apply-diff": _cli.cmd_spec_apply_diff,
            "impact": _cli.cmd_spec_impact,
        }.get(
            str(getattr(a, "spec_command", "")),
            lambda args: (
                print("Usage: factory spec {generate,validate,scope,update,apply-diff,impact}") or 1
            ),
        )(a),
        "workflow": lambda a: __import__(
            "factory.workflow.cli", fromlist=["cmd_workflow"]
        ).cmd_workflow(a),
        "graph": lambda a: {
            "extract": _cli.cmd_graph_extract,
            "update": _cli.cmd_graph_update,
            "status": _cli.cmd_graph_status,
        }.get(
            str(getattr(a, "graph_command", "")),
            lambda args: print("Usage: factory graph {extract,update,status}") or 1,
        )(a),
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
