"""Composition parser — turns a mode spec string into typed execution steps."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

BUILTIN_MODES: frozenset[str] = frozenset({
    "discover",
    "review",
    "improve",
    "build",
    "research",
    "meta",
    "design",
    "refine",
    "create",
    "founder",
    "evolve",
    "spec-generate",
    "spec-update",
    "doc-generate",
    "doc-update",
    "parallel-improve",
    "frontend-design",
    "frontend-design-discover",
    "frontend-design-scan",
    "skill-refine",
})


class SequentialStep(BaseModel):
    """A single mode executed sequentially."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["sequential"] = "sequential"
    mode: str


class ParallelStep(BaseModel):
    """Multiple modes executed in parallel."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["parallel"] = "parallel"
    modes: list[str]


CompositionStep = Annotated[
    Union[SequentialStep, ParallelStep],
    Field(discriminator="type"),
]


def parse_mode_spec(spec: str) -> list[SequentialStep | ParallelStep]:
    """Parse a mode spec string into composition steps.

    Syntax: comma separates sequential stages, plus separates parallel modes
    within a stage. Example: ``"discover,a+b,improve"`` yields
    ``[Sequential(discover), Parallel([a, b]), Sequential(improve)]``.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Mode spec must not be empty")

    steps: list[SequentialStep | ParallelStep] = []
    stages = spec.split(",")

    for stage in stages:
        stage = stage.strip()
        if not stage:
            raise ValueError("Empty stage in mode spec (consecutive or trailing commas)")

        modes = [m.strip() for m in stage.split("+")]
        modes = [m for m in modes if m]

        if not modes:
            raise ValueError(f"Empty mode name in stage: {stage!r}")

        if len(modes) == 1:
            steps.append(SequentialStep(mode=modes[0]))
        else:
            steps.append(ParallelStep(modes=modes))

    return steps


def validate_composition(
    steps: list[SequentialStep | ParallelStep],
    registry_names: set[str] | None = None,
) -> list[str]:
    """Validate a parsed composition. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    if not steps:
        errors.append("Composition must have at least one step")
        return errors

    for i, step in enumerate(steps):
        if isinstance(step, SequentialStep):
            if not step.mode:
                errors.append(f"Step {i}: empty mode name")
            if registry_names is not None and step.mode not in registry_names:
                errors.append(f"Step {i}: unknown mode {step.mode!r}")
        elif isinstance(step, ParallelStep):
            if not step.modes:
                errors.append(f"Step {i}: parallel step has no modes")
            for mode in step.modes:
                if not mode:
                    errors.append(f"Step {i}: empty mode name in parallel step")
                if mode in BUILTIN_MODES:
                    errors.append(
                        f"Step {i}: built-in mode {mode!r} cannot appear in a ParallelStep"
                    )
                if registry_names is not None and mode not in registry_names:
                    errors.append(f"Step {i}: unknown mode {mode!r}")

    return errors
