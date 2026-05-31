"""Protocols for meta-harness component contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from factory.harness.models import ProjectContext, StateBinding, StateRecord, WorkItem


@runtime_checkable
class WorkItemSource(Protocol):
    """Source capable of resolving a user reference into a work item."""

    source_name: str

    def supports(self, ref: str) -> bool:
        """Return whether this source can parse or resolve ``ref``."""
        ...

    def resolve(self, ref: str, project: ProjectContext) -> WorkItem:
        """Resolve ``ref`` into a normalized work item."""
        ...


@runtime_checkable
class WorkerRuntime(Protocol):
    """Runtime capable of invoking an agent/worker for a project context."""

    name: str

    async def invoke(
        self,
        role: str,
        task: str,
        project: ProjectContext,
        *,
        timeout: float = 600.0,
        options: Mapping[str, object] | None = None,
    ) -> tuple[str, int]:
        """Run a role against a task and return output plus exit code."""
        ...


@runtime_checkable
class StateBackend(Protocol):
    """State substrate bound to a project context."""

    name: str

    def describe(self, project: ProjectContext) -> StateBinding:
        """Describe where this backend stores state for ``project``."""
        ...

    def list_record_refs(self, project: ProjectContext) -> Sequence[StateRecord]:
        """Return state record descriptors without mutating state."""
        ...


@runtime_checkable
class GuardrailProvider(Protocol):
    """Provider of validation/check surfaces."""

    name: str

    def describe_checks(self, project: ProjectContext) -> Sequence[str]:
        """Describe checks applicable to ``project``."""
        ...


@runtime_checkable
class DistributionEmitter(Protocol):
    """Emitter for generated distribution artifacts."""

    target: str

    def emit_role(self, role: str) -> str:
        """Emit generated content for an agent role."""
        ...
