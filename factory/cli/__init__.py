"""CLI entry point for the factory — argparse subcommands wrapping library functions."""

from __future__ import annotations

from factory.cli._helpers import CEO_MODES as CEO_MODES
from factory.cli._helpers import RUN_MODES as RUN_MODES
from factory.cli._helpers import _emit_cli_event as _emit_cli_event
from factory.cli._helpers import _is_github_url as _is_github_url
from factory.cli._helpers import _print_banner as _print_banner
from factory.cli._helpers import _show_spinner as _show_spinner
from factory.cli._main import _COMMAND_GROUPS as _COMMAND_GROUPS
from factory.cli._main import build_parser as build_parser
from factory.cli._main import main as main
from factory.cli._wizard import (
    _CLI_REF as _CLI_REF,
    _ask_follow_ups as _ask_follow_ups,
    _classify_with_llm as _classify_with_llm,
    _quick_classify as _quick_classify,
    _substitute_answers as _substitute_answers,
    _welcome_wizard as _welcome_wizard,
)
from factory.cli.admin import (
    cmd_config as cmd_config,
    cmd_detect as cmd_detect,
    cmd_discover as cmd_discover,
    cmd_emit as cmd_emit,
    cmd_home as cmd_home,
    cmd_init as cmd_init,
    cmd_install as cmd_install,
    cmd_log as cmd_log,
    cmd_notify as cmd_notify,
    cmd_profile as cmd_profile,
    cmd_self_update as cmd_self_update,
    cmd_study as cmd_study,
    cmd_usage as cmd_usage,
)
from factory.cli.agents import (
    cmd_ace as cmd_ace,
    cmd_ace_stats as cmd_ace_stats,
    cmd_agent as cmd_agent,
    cmd_runners_list as cmd_runners_list,
)
from factory.cli.backlog import (
    cmd_backlog_add as cmd_backlog_add,
    cmd_backlog_list as cmd_backlog_list,
    cmd_backlog_remove as cmd_backlog_remove,
)
from factory.cli._ceo_dispatch import (
    _start_ceo_tailer as _start_ceo_tailer,
    _stop_ceo_tailer as _stop_ceo_tailer,
)
from factory.cli._mode_handlers import (
    _auto_detect_mode as _auto_detect_mode,
    _resolve_background as _resolve_background,
    _resolve_bg_agents as _resolve_bg_agents,
    _resolve_model as _resolve_model,
)
from factory.cli._path_resolver import (
    _dedupe_project_path as _dedupe_project_path,
    _ensure_repo as _ensure_repo,
    _extract_project_name as _extract_project_name,
    _get_projects_dir as _get_projects_dir,
    _has_research_target as _has_research_target,
    _is_scaffold_only as _is_scaffold_only,
    _materialize_project as _materialize_project,
    _persist_spec as _persist_spec,
    _resolve_focus_issue as _resolve_focus_issue,
    _resolve_input as _resolve_input,
    _slugify as _slugify,
)
from factory.cli._task_builder import (
    _build_ceo_task as _build_ceo_task,
)
from factory.cli.ceo import (
    _build_tmux_run_args as _build_tmux_run_args,
    _tmux_session_alive as _tmux_session_alive,
    _tmux_session_name as _tmux_session_name,
    cmd_ceo as cmd_ceo,
    cmd_refactory as cmd_refactory,
    cmd_tmux as cmd_tmux,
    cmd_tmux_capture as cmd_tmux_capture,
    cmd_tmux_ls as cmd_tmux_ls,
    cmd_tmux_stop as cmd_tmux_stop,
)
from factory.cli.run import (
    cmd_run as cmd_run,
)
from factory.cli.eval_cmds import (
    cmd_baseline as cmd_baseline,
    cmd_eval as cmd_eval,
    cmd_guard as cmd_guard,
    cmd_precheck as cmd_precheck,
)
from factory.cli.infra import (
    cmd_archive as cmd_archive,
    cmd_backfill_archive as cmd_backfill_archive,
    cmd_checkpoint as cmd_checkpoint,
    cmd_dashboard as cmd_dashboard,
    cmd_resume as cmd_resume,
    cmd_serve_mcp as cmd_serve_mcp,
    cmd_vault_init as cmd_vault_init,
)
from factory.cli.registry import (
    cmd_digest as cmd_digest,
    cmd_insights as cmd_insights,
    cmd_registry_list as cmd_registry_list,
    cmd_report_update as cmd_report_update,
)
from factory.cli.research import (
    cmd_backfill_citations as cmd_backfill_citations,
    cmd_leakage_check as cmd_leakage_check,
    cmd_research as cmd_research,
    cmd_validate_research as cmd_validate_research,
)
from factory.cli.review import (
    cmd_clean_pr as cmd_clean_pr,
    cmd_refine_begin as cmd_refine_begin,
    cmd_refine_complete as cmd_refine_complete,
    cmd_refine_status as cmd_refine_status,
    cmd_review as cmd_review,
)
from factory.cli.store import (
    cmd_begin as cmd_begin,
    cmd_diff as cmd_diff,
    cmd_explain as cmd_explain,
    cmd_export as cmd_export,
    cmd_finalize as cmd_finalize,
    cmd_history as cmd_history,
    cmd_message as cmd_message,
    cmd_status as cmd_status,
    cmd_summary as cmd_summary,
)


if __name__ == "__main__":
    raise SystemExit(main())
