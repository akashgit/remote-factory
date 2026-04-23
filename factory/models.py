"""All Pydantic v2 strict models for the remote factory."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


# ── project state ─────────────────────────────────────────────────


class ProjectState(str, Enum):
    """The five possible states of a target project."""

    NO_REPO = "no_repo"
    REPO_INCOMPLETE = "incomplete"
    NO_FACTORY = "no_factory"
    EVALS_PENDING_REVIEW = "evals_pending_review"
    HAS_FACTORY = "has_factory"


# ── factory config ────────────────────────────────────────────────


class FactoryConfig(BaseModel):
    """Machine-readable config stored at .factory/config.json."""

    model_config = ConfigDict(strict=True, extra="forbid")

    goal: str
    scope: list[str]
    guards: list[str]
    eval_command: str
    eval_threshold: float
    constraints: list[str]


# ── eval ──────────────────────────────────────────────────────────


class EvalResult(BaseModel):
    """Single eval output from one eval function."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    score: float
    weight: float
    passed: bool
    details: str


class CompositeScore(BaseModel):
    """Aggregated result from all evals + guards."""

    model_config = ConfigDict(strict=True, extra="forbid")

    total: float
    results: list[EvalResult]
    guard_violations: list[str]
    passed: bool


# ── eval discovery ────────────────────────────────────────────────


class EvalDimension(BaseModel):
    """One discovered or user-provided eval dimension."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    command: str
    weight: float
    parser: Literal["exit_code", "json", "regex"]
    regex_pattern: str | None = None
    description: str
    source: Literal["explicit", "discovered", "researched", "fallback"]


class EvalProfile(BaseModel):
    """Complete eval profile for a project, built by the Researcher agent."""

    model_config = ConfigDict(strict=True, extra="forbid")

    project_type: str
    dimensions: list[EvalDimension]
    tier: Literal["explicit", "discovered", "researched", "fallback"]
    confidence: float
    human_reviewed: bool = False


class ProjectProfile(BaseModel):
    """Project metadata discovered during introspection."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    language: str
    framework: str | None = None
    project_type: str
    has_tests: bool
    has_linter: bool
    has_type_checker: bool
    has_ci: bool
    test_command: str | None = None
    lint_command: str | None = None
    type_check_command: str | None = None
    package_manager: str | None = None


# ── experiments ───────────────────────────────────────────────────


class Hypothesis(BaseModel):
    """A proposed change generated during the observe/hypothesize phase."""

    model_config = ConfigDict(strict=True, extra="forbid")

    description: str
    rationale: str
    expected_impact: str
    target_files: list[str]


class ExperimentRecord(BaseModel):
    """One row in results.tsv + the experiment directory."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: int
    timestamp: datetime
    hypothesis: str
    change_summary: str
    issue_number: int | None
    pr_number: int | None
    score_before: float | None
    score_after: float | None
    delta: float | None
    verdict: Literal["keep", "revert", "error"]
    cost_usd: float | None
    notes: str
    research_citations: list[str] = []


# ── cross-project insights ───────────────────────────────────────


class HypothesisOutcome(BaseModel):
    """A hypothesis paired with its outcome, for cross-project pattern analysis."""

    model_config = ConfigDict(strict=True, extra="forbid")

    hypothesis: str
    verdict: Literal["keep", "revert", "error"]
    category: str
    project: str
    delta: float | None = None


class ProjectSummary(BaseModel):
    """Summary of a factory-managed project's experiment history."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    experiment_count: int
    keep_count: int
    revert_count: int
    error_count: int
    keep_rate: float
    latest_score: float | None = None


class Pattern(BaseModel):
    """A recurring pattern discovered across projects."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str
    evidence: list[str]
    confidence: float


class CrossProjectInsights(BaseModel):
    """Aggregated cross-project analysis for the Strategist."""

    model_config = ConfigDict(strict=True, extra="forbid")

    projects: list[ProjectSummary]
    outcomes: list[HypothesisOutcome]
    category_stats: dict[str, dict[str, float]]
    winning_categories: list[str]
    losing_categories: list[str]
    patterns: list[Pattern]
    generated_at: datetime


# ── cost tracking ─────────────────────────────────────────────────


class CostBudget(BaseModel):
    """Cost guardrails for factory sessions."""

    model_config = ConfigDict(strict=True, extra="forbid")

    per_experiment_max: float = 2.0
    per_session_max: float = 10.0
    per_month_max: float = 100.0
    current_session_spent: float = 0.0
    current_month_spent: float = 0.0


# ── protocols ─────────────────────────────────────────────────────


@runtime_checkable
class Notifier(Protocol):
    """Interface for sending experiment digests."""

    async def send_digest(
        self,
        project_name: str,
        records: list[ExperimentRecord],
        composite: CompositeScore | None,
    ) -> None: ...
