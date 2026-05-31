"""Tests for additive harness domain models."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.harness.models import (
    ConflictStatus,
    DecisionKind,
    DecisionRecord,
    DistributionBundle,
    EvidenceKind,
    EvidenceRef,
    ProjectContext,
    RepoBinding,
    StateConflict,
    StateRecord,
    StateSource,
    WorkItem,
    WorkItemKind,
)


def test_project_context_separates_project_from_repo(tmp_path):
    repo = RepoBinding(repo_id="api", path=tmp_path, role="primary")
    project = ProjectContext(
        project_id="proj-1",
        name="Example",
        root=tmp_path,
        repo_bindings=[repo],
    )

    assert project.project_id == "proj-1"
    assert project.primary_repo().repo_id == "api"
    assert project.primary_repo().path == tmp_path


def test_project_context_rejects_unknown_fields(tmp_path):
    with pytest.raises(ValidationError):
        ProjectContext(
            project_id="proj-1",
            name="Example",
            root=tmp_path,
            unexpected=True,
        )


def test_primary_repo_raises_when_unbound(tmp_path):
    project = ProjectContext(project_id="proj-1", name="Example", root=tmp_path)

    with pytest.raises(ValueError, match="no repo bindings"):
        project.primary_repo()


def test_work_item_can_reference_external_state():
    item = WorkItem(
        work_item_id="github-issue-42",
        kind=WorkItemKind.ISSUE,
        title="Fix login",
        labels=["bug"],
    )

    assert item.kind == WorkItemKind.ISSUE
    assert item.labels == ["bug"]


def test_state_record_contains_merge_metadata():
    record = StateRecord(
        id="experiment:1",
        kind="experiment",
        project_id="proj-1",
        repo_id="api",
        source=StateSource.LOCAL_FACTORY,
        parent_ids=["event:0"],
        payload={"verdict": "keep"},
    )

    assert record.revision == 1
    assert record.parent_ids == ["event:0"]
    assert record.payload["verdict"] == "keep"


def test_state_conflict_is_first_class():
    conflict = StateConflict(
        conflict_id="conflict-1",
        record_id="config:main",
        project_id="proj-1",
        kind="config",
        sources=["local_factory", "linear"],
        reason="concurrent contract edits",
    )

    assert conflict.status == ConflictStatus.OPEN


def test_evidence_and_decision_records_link_by_ids():
    evidence = EvidenceRef(
        evidence_id="diff-1",
        kind=EvidenceKind.DIFF,
        uri="file://changes.diff",
        project_id="proj-1",
    )
    decision = DecisionRecord(
        decision_id="decision-1",
        project_id="proj-1",
        decision=DecisionKind.KEEP,
        rationale="Score improved",
        evidence_ids=[evidence.evidence_id],
    )

    assert decision.evidence_ids == ["diff-1"]


def test_distribution_bundle_is_component_assembly():
    bundle = DistributionBundle(
        name="cli-local",
        description="CLI local bundle",
        surface="factory CLI",
        runtime="local_agent_runtime",
        state_backend="local_factory_state",
        guardrails=["precheck"],
        emitters=["claude"],
    )

    assert bundle.runtime == "local_agent_runtime"
    assert bundle.state_backend == "local_factory_state"


def test_repo_binding_requires_path_instance():
    with pytest.raises(ValidationError):
        RepoBinding(path="/tmp/not-coerced")

    assert RepoBinding(path=Path("/tmp/ok")).path == Path("/tmp/ok")
