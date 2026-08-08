"""The runtime record — one shape for a podman container and for a cluster pod.

This lives apart from `lifecycle` because both sides of the boundary need it and neither is
below the other: `lifecycle` builds these records from podman and `k8s` builds them from the
cluster, while `lifecycle` in turn asks `k8s` for the cluster half of `ls`. Holding the type in
either module makes that mutual, and the import then has to be deferred into a function body to
survive — a workaround that hides a genuinely circular dependency rather than removing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# States in which nothing a delete could interrupt is still happening. Anything else — including a
# state we have never seen, or a blank one — is treated as active, which is the safe default for a
# check that guards a destructive operation.
_INACTIVE_STATES = frozenset({"exited", "stopped", "created", "dead", "removing", "succeeded",
                              "failed", "terminated", "error", "completed"})


@dataclass(frozen=True)
class Runtime:
    """One factory-created runtime, normalized across podman containers and cluster pods."""

    name: str
    target: str
    project: str
    state: str
    created: datetime | None = None
    source: str | None = None

    @property
    def active(self) -> bool:
        return self.state.strip().lower() not in _INACTIVE_STATES


class LifecycleError(RuntimeError):
    """Listing or acting on a runtime failed in a way the caller should report."""
