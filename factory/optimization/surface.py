"""Surface — unified mutable surface for optimization loops.

Composes frozen_nodes (from InnerLoop), prompt_slots (for SkillOpt),
and inner/outer_surfaces (from OuterLoopConfig) into a single descriptor.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from factory.models import FactoryConfig
from factory.workflow.primitives import Workflow


@dataclass
class Surface:
    """Describes what an optimization loop can mutate."""

    workflow: Workflow | None = None
    frozen_nodes: frozenset[str] = frozenset()
    prompt_slots: dict[str, str] = field(default_factory=dict)
    inner_surfaces: list[str] = field(default_factory=list)
    outer_surfaces: list[str] = field(default_factory=list)

    def mutable_prompt_slots(self) -> dict[str, str]:
        """Return all prompt slots (all are mutable for now)."""
        return dict(self.prompt_slots)

    def mutable_nodes(self) -> set[str]:
        """Return the set of node IDs the outer loop may modify."""
        if self.workflow is None:
            return set()
        return set(self.workflow.nodes.keys()) - self.frozen_nodes

    def validate(self) -> list[str]:
        """Check frozen_nodes against workflow, return list of issues."""
        issues: list[str] = []
        if not self.frozen_nodes or self.workflow is None:
            return issues
        invalid = self.frozen_nodes - self.workflow.nodes.keys()
        if invalid:
            issues.append(
                f"frozen_nodes contains IDs not in workflow.nodes: {sorted(invalid)}"
            )
        if self.workflow and len(self.frozen_nodes) == len(self.workflow.nodes):
            issues.append("All nodes are frozen — outer loop has no mutable surface")
            warnings.warn(
                "All nodes are frozen — outer loop has no mutable surface",
                stacklevel=2,
            )
        return issues

    @classmethod
    def from_config(
        cls,
        config: FactoryConfig,
        workflow: Workflow | None = None,
    ) -> Surface:
        """Build a Surface from a FactoryConfig."""
        frozen: frozenset[str] = frozenset()
        inner: list[str] = []
        outer: list[str] = []

        if config.inner_loop and hasattr(config.inner_loop, "frozen_nodes"):
            frozen = frozenset(getattr(config.inner_loop, "frozen_nodes", []))

        if config.outer_loop:
            inner = list(config.outer_loop.inner_surfaces)
            outer = list(config.outer_loop.outer_surfaces)

        return cls(
            workflow=workflow,
            frozen_nodes=frozen,
            inner_surfaces=inner,
            outer_surfaces=outer,
        )
