"""Additive meta-harness domain models.

These models intentionally do not replace the persisted models in
``factory.models``. They describe the component abstraction used by Phase 0
wrappers and future refactors.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class WorkItemKind(str, Enum):
    """Kinds of work that can enter the SDLC lifecycle."""

    PROMPT = "prompt"
    FOCUS = "focus"
    BACKLOG = "backlog"
    ISSUE = "issue"
    TICKET = "ticket"
    RESEARCH_TARGET = "research_target"


class StateSource(str, Enum):
    """Known state substrates."""

    LOCAL_FACTORY = "local_factory"
    GITHUB = "github"
    GITLAB = "gitlab"
    JIRA = "jira"
    LINEAR = "linear"
    MANAGED = "managed"


class EvidenceKind(str, Enum):
    """Kinds of evidence collected during a cycle."""

    DIFF = "diff"
    LOG = "log"
    EVAL = "eval"
    REVIEW = "review"
    REPORT = "report"
    ARTIFACT = "artifact"
    CI = "ci"


class DecisionKind(str, Enum):
    """Decision outcomes for a work item or experiment."""

    KEEP = "keep"
    REVERT = "revert"
    MERGE = "merge"
    PARK = "park"
    RETRY = "retry"
    ESCALATE = "escalate"
    ERROR = "error"


class ConflictStatus(str, Enum):
    """Resolution state for multi-user/state-store conflicts."""

    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class RepoBinding(BaseModel):
    """A repository or worktree bound into a project context."""

    model_config = ConfigDict(strict=True, extra="forbid")

    repo_id: str = "primary"
    path: Path
    role: str = "primary"
    remote: str | None = None
    default_branch: str | None = None
    worktree: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)


class StateBinding(BaseModel):
    """A state substrate bound to a project."""

    model_config = ConfigDict(strict=True, extra="forbid")

    binding_id: str
    source: StateSource
    location: str
    role: str = "primary"
    metadata: dict[str, str] = Field(default_factory=dict)


class ExternalStateRef(BaseModel):
    """Reference to an external issue, PR, ticket, or managed-state object."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source: StateSource
    external_id: str
    url: str = ""
    status: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)


class WorkItem(BaseModel):
    """A unit of work entering the harness lifecycle."""

    model_config = ConfigDict(strict=True, extra="forbid")

    work_item_id: str
    kind: WorkItemKind
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    repo_ids: list[str] = Field(default_factory=list)
    refs: list[ExternalStateRef] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class ProjectContext(BaseModel):
    """Durable SDLC boundary, separate from runtime or distribution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    project_id: str
    name: str
    root: Path
    goal: str = ""
    repo_bindings: list[RepoBinding] = Field(default_factory=list)
    state_bindings: list[StateBinding] = Field(default_factory=list)
    work_items: list[WorkItem] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    def primary_repo(self) -> RepoBinding:
        """Return the primary repo binding.

        Raises:
            ValueError: If the project has no repository bindings.
        """
        if not self.repo_bindings:
            raise ValueError(f"Project {self.project_id!r} has no repo bindings")
        for repo in self.repo_bindings:
            if repo.role == "primary":
                return repo
        return self.repo_bindings[0]


class ExecutionContract(BaseModel):
    """Scope and policy for executing a work item."""

    model_config = ConfigDict(strict=True, extra="forbid")

    contract_id: str
    project_id: str
    work_item_id: str | None = None
    scope: str = ""
    mutable_surfaces: list[str] = Field(default_factory=list)
    fixed_surfaces: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    report_schema: list[str] = Field(default_factory=list)
    budget: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    """Pointer to immutable evidence collected during execution or validation."""

    model_config = ConfigDict(strict=True, extra="forbid")

    evidence_id: str
    kind: EvidenceKind
    uri: str
    project_id: str
    repo_id: str | None = None
    work_item_id: str | None = None
    immutable: bool = True
    created_at: datetime = Field(default_factory=_now_utc)
    metadata: dict[str, str] = Field(default_factory=dict)


class DecisionRecord(BaseModel):
    """Decision made from evidence and guardrail results."""

    model_config = ConfigDict(strict=True, extra="forbid")

    decision_id: str
    project_id: str
    decision: DecisionKind
    rationale: str
    repo_id: str | None = None
    work_item_id: str | None = None
    actor: str = "factory"
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now_utc)
    metadata: dict[str, str] = Field(default_factory=dict)


class MemoryRef(BaseModel):
    """Pointer to durable project memory."""

    model_config = ConfigDict(strict=True, extra="forbid")

    memory_id: str
    project_id: str
    kind: str
    uri: str
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now_utc)
    metadata: dict[str, str] = Field(default_factory=dict)


class StateRecord(BaseModel):
    """Merge-ready state record descriptor."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    kind: str
    project_id: str
    source: StateSource
    actor: str = "factory"
    repo_id: str | None = None
    revision: int = 1
    parent_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now_utc)
    updated_at: datetime = Field(default_factory=_now_utc)
    payload: dict[str, str] = Field(default_factory=dict)


class StateConflict(BaseModel):
    """First-class conflict record for state merge/resolution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    conflict_id: str
    record_id: str
    project_id: str
    kind: str
    sources: list[str] = Field(default_factory=list)
    reason: str
    status: ConflictStatus = ConflictStatus.OPEN
    created_at: datetime = Field(default_factory=_now_utc)
    metadata: dict[str, str] = Field(default_factory=dict)


class DistributionBundle(BaseModel):
    """A named assembly of component implementations."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str
    surface: str
    runtime: str
    state_backend: str
    guardrails: list[str] = Field(default_factory=list)
    emitters: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
