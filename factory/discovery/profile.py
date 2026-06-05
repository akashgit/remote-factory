"""Build an EvalProfile from a ProjectProfile — the bridge between introspection and eval generation."""

from __future__ import annotations

from typing import Literal

import structlog

from factory.models import EvalDimension, EvalProfile, ProjectProfile

log = structlog.get_logger()

EvalTier = Literal["explicit", "discovered", "researched", "fallback"]


def build_eval_profile(project: ProjectProfile) -> EvalProfile:
    """Build an EvalProfile from discovered project metadata.

    Uses the 3-tier resolution:
      - Tier 2 (discovered): tools found in project config
      - Tier 3 (researched): inferred from project type
      - Tier 0 (fallback): minimal build/import checks
    """
    dimensions: list[EvalDimension] = []
    tier: EvalTier = "fallback"

    # Tier 2: Discovered evals from existing tooling
    if project.test_command:
        dimensions.append(EvalDimension(
            name="tests",
            command=project.test_command,
            weight=0.5,
            parser="exit_code",
            description=f"Run test suite: {project.test_command}",
            source="discovered",
        ))
        tier = "discovered"

    if project.lint_command:
        dimensions.append(EvalDimension(
            name="lint",
            command=project.lint_command,
            weight=0.3 if project.test_command else 0.5,
            parser="exit_code",
            description=f"Run linter: {project.lint_command}",
            source="discovered",
        ))
        if tier == "fallback":
            tier = "discovered"

    # Tier 3: Researched evals based on project type
    if project.type_check_command:
        dimensions.append(EvalDimension(
            name="type_check",
            command=project.type_check_command,
            weight=0.15,
            parser="exit_code",
            description=f"Run type checker: {project.type_check_command}",
            source="researched",
        ))
        if tier == "fallback":
            tier = "researched"

    # Add coverage eval if tests exist but coverage isn't tracked
    if project.has_tests and project.language == "python":
        # Find the main package for coverage target
        coverage_target = project.name.replace("-", "_")
        pm = "uv run" if project.package_manager == "uv" else "python -m"
        dimensions.append(EvalDimension(
            name="coverage",
            command=f"{pm} pytest --cov={coverage_target} --cov-report=term -q",
            weight=0.15,
            parser="exit_code",
            description="Measure test coverage",
            source="researched",
        ))

    # Tier 0: Fallback — minimal checks
    if not dimensions:
        if project.language == "python":
            dimensions.append(EvalDimension(
                name="import_check",
                command=f"python -c 'import {project.name.replace('-', '_')}'",
                weight=0.5,
                parser="exit_code",
                description="Verify the package can be imported",
                source="fallback",
            ))
        dimensions.append(EvalDimension(
            name="syntax_check",
            command=_syntax_check_command(project),
            weight=0.5,
            parser="exit_code",
            description="Verify code has no syntax errors",
            source="fallback",
        ))

    # Always add observability coverage (universally applicable, inline eval)
    dimensions.append(EvalDimension(
        name="observability",
        command="(inline)",
        weight=0.10,
        parser="json",
        description="Analyze logging coverage, structured logging, and request tracing",
        source="researched",
    ))

    # Normalize weights to sum to 1.0
    weight_sum = sum(d.weight for d in dimensions)
    if weight_sum > 0 and abs(weight_sum - 1.0) > 1e-9:
        dimensions = [
            d.model_copy(update={"weight": d.weight / weight_sum})
            for d in dimensions
        ]

    confidence = {
        "explicit": 1.0,
        "discovered": 0.8,
        "researched": 0.5,
        "fallback": 0.2,
    }[tier]

    log.info(
        "build_eval_profile_complete",
        project=project.name,
        tier=tier,
        confidence=confidence,
        dimension_count=len(dimensions),
        dimensions=[d.name for d in dimensions],
    )
    return EvalProfile(
        project_type=project.project_type,
        dimensions=dimensions,
        tier=tier,
        confidence=confidence,
    )


def _syntax_check_command(project: ProjectProfile) -> str:
    """Return a syntax check command appropriate for the project language."""
    if project.language == "python":
        return "python -m py_compile $(find . -name '*.py' -not -path './.venv/*')"
    if project.language == "typescript":
        return "npx tsc --noEmit"
    if project.language == "rust":
        return "cargo check"
    if project.language == "go":
        return "go vet ./..."
    return "true"  # no-op fallback
