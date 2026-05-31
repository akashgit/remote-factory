"""Phase 0 wrappers over the current implementation.

These adapters are intentionally not wired into production call sites yet. They
make existing behavior addressable through the component contracts.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast, get_args

from factory.harness.models import (
    ExternalStateRef,
    ProjectContext,
    RepoBinding,
    StateBinding,
    StateRecord,
    StateSource,
    WorkItem,
    WorkItemKind,
)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


class LocalProjectContext:
    """Factory for implicit single-repo project contexts used by the CLI today."""

    @staticmethod
    def from_path(path: Path) -> ProjectContext:
        resolved = path.expanduser().resolve()
        project_id = _stable_id("local", resolved.as_posix())
        repo = RepoBinding(
            repo_id="primary",
            path=resolved,
            role="primary",
            metadata={"source": "cli_path"},
        )
        state = StateBinding(
            binding_id=f"{project_id}:local_factory",
            source=StateSource.LOCAL_FACTORY,
            location=str(resolved / ".factory"),
            role="primary",
        )
        return ProjectContext(
            project_id=project_id,
            name=resolved.name or "project",
            root=resolved,
            repo_bindings=[repo],
            state_bindings=[state],
            metadata={"distribution": "cli-local"},
        )


class GitHubGitLabIssueSource:
    """Work-item source backed by the existing GitHub/GitLab issue helpers."""

    source_name = "github_gitlab_issue"

    def supports(self, ref: str) -> bool:
        from factory.issue import is_issue_ref

        return is_issue_ref(ref)

    def resolve(self, ref: str, project: ProjectContext) -> WorkItem:
        from factory.issue import fetch_issue, format_issue_as_spec

        repo = project.primary_repo()
        spec = fetch_issue(ref, repo.path)
        source = StateSource.GITLAB if spec.forge == "gitlab" else StateSource.GITHUB
        external = ExternalStateRef(
            source=source,
            external_id=str(spec.number),
            url=spec.url,
            status="open",
            metadata={"forge": spec.forge},
        )
        return WorkItem(
            work_item_id=f"{spec.forge}-issue-{spec.number}",
            kind=WorkItemKind.ISSUE,
            title=spec.title,
            body=format_issue_as_spec(spec),
            labels=spec.labels,
            repo_ids=[repo.repo_id],
            refs=[external],
            metadata={"source_ref": ref},
        )


class BacklogWorkItemSource:
    """Simple wrapper for backlog/focus text as a work item."""

    source_name = "backlog_text"

    def supports(self, ref: str) -> bool:
        return bool(ref.strip())

    def resolve(self, ref: str, project: ProjectContext) -> WorkItem:
        title = ref.strip()
        return WorkItem(
            work_item_id=_stable_id("backlog", f"{project.project_id}:{title}"),
            kind=WorkItemKind.BACKLOG,
            title=title,
            repo_ids=[project.primary_repo().repo_id],
            metadata={"source": "backlog_or_focus"},
        )


class LocalAgentRuntimeAdapter:
    """Worker runtime wrapper around ``factory.agents.runner.invoke_agent``."""

    name = "local_agent_runtime"

    async def invoke(
        self,
        role: str,
        task: str,
        project: ProjectContext,
        *,
        timeout: float = 600.0,
        options: Mapping[str, object] | None = None,
    ) -> tuple[str, int]:
        from factory.agents.runner import AgentRole, invoke_agent

        role_values = get_args(AgentRole)
        if role not in role_values:
            raise ValueError(f"Unknown agent role: {role!r}")

        opts = dict(options or {})
        return await invoke_agent(
            cast(AgentRole, role),
            task,
            project.primary_repo().path,
            timeout=timeout,
            dangerously_skip_permissions=bool(opts.get("dangerously_skip_permissions", True)),
            model=cast(str | None, opts.get("model")),
            runner_name=cast(str | None, opts.get("runner_name")),
            session_name=cast(str | None, opts.get("session_name")),
            use_profile=bool(opts.get("use_profile", False)),
        )


class LocalFactoryStateAdapter:
    """Read-only descriptor wrapper for current local `.factory` state."""

    name = "local_factory_state"

    def describe(self, project: ProjectContext) -> StateBinding:
        return StateBinding(
            binding_id=f"{project.project_id}:local_factory",
            source=StateSource.LOCAL_FACTORY,
            location=str(project.root / ".factory"),
            role="primary",
        )

    def list_record_refs(self, project: ProjectContext) -> list[StateRecord]:
        results_path = project.root / ".factory" / "results.tsv"
        if not results_path.exists():
            return []

        records: list[StateRecord] = []
        with results_path.open(newline="") as f:
            reader = csv.DictReader(f, dialect="excel-tab")
            for row in reader:
                exp_id = row.get("id") or str(len(records) + 1)
                timestamp = _parse_datetime(row.get("timestamp"))
                payload = {k: v for k, v in row.items() if v is not None}
                records.append(
                    StateRecord(
                        id=f"experiment:{exp_id}",
                        kind="experiment",
                        project_id=project.project_id,
                        repo_id=project.primary_repo().repo_id,
                        source=StateSource.LOCAL_FACTORY,
                        actor="factory",
                        created_at=timestamp,
                        updated_at=timestamp,
                        payload=payload,
                    )
                )
        return records


class CurrentGuardrailAdapter:
    """Descriptor wrapper for current local guardrail surfaces."""

    name = "current_guardrails"

    def describe_checks(self, project: ProjectContext) -> list[str]:
        checks = [
            "eval.runner",
            "precheck",
            "hard_constraints",
            "leakage",
            "clean_pr",
        ]
        config_path = project.root / ".factory" / "config.json"
        if not config_path.exists():
            return checks

        try:
            data = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError):
            return checks

        eval_command = data.get("eval_command")
        if isinstance(eval_command, str) and eval_command:
            checks.append(f"eval_command:{eval_command}")

        hard_constraints = data.get("hard_constraints")
        if isinstance(hard_constraints, list):
            for item in hard_constraints:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    checks.append(f"hard_constraint:{item['name']}")

        return checks


class CurrentAgentDistributionAdapter:
    """Emitter wrapper around the current agent generation functions."""

    def __init__(self, target: str = "claude") -> None:
        if target not in {"claude", "codex"}:
            raise ValueError(f"Unsupported distribution target: {target!r}")
        self.target = target

    def emit_role(self, role: str) -> str:
        from factory.agents.plugin import generate_agent_content, generate_codex_agent_toml

        if self.target == "codex":
            return generate_codex_agent_toml(role)
        return generate_agent_content(role)
