"""Tests for Phase 0 wrappers over current implementation."""

import asyncio
from pathlib import Path

import pytest

from factory.harness.adapters import (
    BacklogWorkItemSource,
    CurrentGuardrailAdapter,
    GitHubGitLabIssueSource,
    LocalAgentRuntimeAdapter,
    LocalFactoryStateAdapter,
    LocalProjectContext,
)
from factory.harness.models import StateSource, WorkItemKind
from factory.issue import IssueSpec


def test_local_project_context_from_path(tmp_path):
    project = LocalProjectContext.from_path(tmp_path)

    assert project.name == tmp_path.name
    assert project.primary_repo().path == tmp_path.resolve()
    assert project.state_bindings[0].source == StateSource.LOCAL_FACTORY
    assert project.metadata["distribution"] == "cli-local"


def test_backlog_source_normalizes_text(tmp_path):
    project = LocalProjectContext.from_path(tmp_path)
    item = BacklogWorkItemSource().resolve(" add dark mode ", project)

    assert item.kind == WorkItemKind.BACKLOG
    assert item.title == "add dark mode"
    assert item.repo_ids == ["primary"]


def test_issue_source_wraps_existing_issue_helpers(tmp_path, monkeypatch):
    project = LocalProjectContext.from_path(tmp_path)

    def fake_fetch_issue(ref: str, project_path: Path) -> IssueSpec:
        assert ref == "42"
        assert project_path == tmp_path.resolve()
        return IssueSpec(
            number=42,
            title="Fix auth",
            body="Auth is broken",
            labels=["bug"],
            url="https://github.com/acme/app/issues/42",
            forge="github",
        )

    monkeypatch.setattr("factory.issue.fetch_issue", fake_fetch_issue)

    source = GitHubGitLabIssueSource()
    assert source.supports("42")
    item = source.resolve("42", project)

    assert item.work_item_id == "github-issue-42"
    assert item.kind == WorkItemKind.ISSUE
    assert item.title == "Fix auth"
    assert item.labels == ["bug"]
    assert item.refs[0].source == StateSource.GITHUB
    assert "Issue: https://github.com/acme/app/issues/42" in item.body


@pytest.mark.asyncio
async def test_local_agent_runtime_wraps_invoke_agent(tmp_path, monkeypatch):
    project = LocalProjectContext.from_path(tmp_path)
    calls = {}

    async def fake_invoke_agent(role, task, project_path, **kwargs):  # noqa: ANN001, ANN202
        calls["role"] = role
        calls["task"] = task
        calls["project_path"] = project_path
        calls["kwargs"] = kwargs
        return "ok", 0

    monkeypatch.setattr("factory.agents.runner.invoke_agent", fake_invoke_agent)

    output, code = await LocalAgentRuntimeAdapter().invoke(
        "researcher",
        "inspect",
        project,
        timeout=12.0,
        options={"runner_name": "claude"},
    )

    assert (output, code) == ("ok", 0)
    assert calls["role"] == "researcher"
    assert calls["task"] == "inspect"
    assert calls["project_path"] == tmp_path.resolve()
    assert calls["kwargs"]["timeout"] == 12.0
    assert calls["kwargs"]["runner_name"] == "claude"


def test_local_agent_runtime_rejects_unknown_role(tmp_path):
    project = LocalProjectContext.from_path(tmp_path)

    with pytest.raises(ValueError, match="Unknown agent role"):
        asyncio.run(LocalAgentRuntimeAdapter().invoke("nope", "x", project))


def test_local_factory_state_adapter_reads_results_tsv(tmp_path):
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "results.tsv").write_text(
        "id\ttimestamp\thypothesis\tverdict\n"
        "1\t2026-05-27T00:00:00+00:00\ttry x\tkeep\n"
    )
    project = LocalProjectContext.from_path(tmp_path)

    records = LocalFactoryStateAdapter().list_record_refs(project)

    assert len(records) == 1
    assert records[0].id == "experiment:1"
    assert records[0].source == StateSource.LOCAL_FACTORY
    assert records[0].payload["verdict"] == "keep"


def test_guardrail_adapter_describes_default_and_configured_checks(tmp_path):
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "config.json").write_text(
        '{"eval_command": "pytest -q", "hard_constraints": [{"name": "no-secrets"}]}'
    )
    project = LocalProjectContext.from_path(tmp_path)

    checks = CurrentGuardrailAdapter().describe_checks(project)

    assert "precheck" in checks
    assert "eval_command:pytest -q" in checks
    assert "hard_constraint:no-secrets" in checks
