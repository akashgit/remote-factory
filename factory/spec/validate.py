"""Spec validation engine — path checks, import verification, behavioral section checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.spec.parser import RepoSpec, parse_spec

log = structlog.get_logger()


@dataclass
class CouplingMetrics:
    """Coupling metrics for a single module (legacy — kept for backward compat)."""

    afferent: int = 0
    efferent: int = 0
    instability: float = 0.0


@dataclass
class ValidationResult:
    """Result of spec validation."""

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, CouplingMetrics] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def _check_paths(spec: RepoSpec, project_path: Path) -> tuple[list[str], list[str]]:
    """Verify each module's declared path exists on disk."""
    errors: list[str] = []
    warnings: list[str] = []

    for mod in spec.modules:
        if not mod.path:
            warnings.append(f"Module '{mod.name}' has no path declared")
            continue

        full_path = project_path / mod.path
        if not full_path.exists():
            errors.append(f"Module '{mod.name}': path '{mod.path}' does not exist")

    return errors, warnings


async def _check_imports_haiku(spec: RepoSpec, project_path: Path) -> tuple[list[str], list[str]]:
    """Cross-reference declared dependency edges against actual imports using Haiku."""
    from factory.agents.runner import invoke_agent

    errors: list[str] = []
    warnings: list[str] = []

    edges_to_check: list[dict[str, str]] = []
    for mod in spec.modules:
        if not mod.path or not mod.depends_on:
            continue
        mod_path = project_path / mod.path
        if not mod_path.exists():
            continue
        for dep_name in mod.depends_on:
            dep_mod = spec.get_module(dep_name)
            dep_path = dep_mod.path if dep_mod else dep_name
            edges_to_check.append(
                {
                    "source_module": mod.name,
                    "source_path": mod.path,
                    "target_module": dep_name,
                    "target_path": dep_path,
                }
            )

    if not edges_to_check:
        return errors, warnings

    task = (
        f"Verify dependency edges in {project_path}.\n\n"
        "For each declared dependency edge below, read the source module's files "
        "and confirm that it actually imports from the target module.\n\n"
        "## Edges to Verify\n\n"
        f"```json\n{json.dumps(edges_to_check, indent=2)}\n```\n\n"
        "## Output\n\n"
        "Return a JSON array of findings. Each finding is an object with:\n"
        '- "type": "phantom" (declared in spec but not found in source) or '
        '"missing" (found in source but not declared in spec)\n'
        '- "source": source module name\n'
        '- "target": target module name\n'
        '- "detail": brief explanation\n\n'
        "If all edges are verified, return an empty array: []\n"
        "Return ONLY the JSON array, no other text."
    )

    result, code = await invoke_agent(
        "researcher",
        task,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        warnings.append(f"Haiku import verification failed (exit {code})")
        return errors, warnings

    try:
        result_text = result.strip()
        start = result_text.find("[")
        end = result_text.rfind("]") + 1
        if start >= 0 and end > start:
            findings = json.loads(result_text[start:end])
        else:
            findings = json.loads(result_text)

        for finding in findings:
            finding_type = finding.get("type", "")
            source = finding.get("source", "")
            target = finding.get("target", "")
            detail = finding.get("detail", "")
            msg = f"Module '{source}' → '{target}': {detail}"
            if finding_type == "phantom":
                warnings.append(f"Phantom edge: {msg}")
            elif finding_type == "missing":
                warnings.append(f"Missing edge: {msg}")
    except (json.JSONDecodeError, TypeError, KeyError):
        warnings.append("Could not parse Haiku import verification output")

    return errors, warnings


def _detect_orphans(spec: RepoSpec) -> list[str]:
    """Flag modules with zero consumers (no incoming dependency edges or consumed_by)."""
    warnings: list[str] = []
    all_names = {m.name for m in spec.modules}
    consumed: set[str] = set()

    for mod in spec.modules:
        for dep in mod.depends_on:
            if dep in all_names:
                consumed.add(dep)
        if mod.consumed_by:
            consumed.add(mod.name)

    for edge in spec.dependency_edges:
        if edge.target in all_names:
            consumed.add(edge.target)

    for mod in spec.modules:
        if mod.name not in consumed and mod.name in all_names:
            warnings.append(f"Orphan module: '{mod.name}' has zero consumers")

    return warnings


def _detect_hubs(spec: RepoSpec) -> list[str]:
    """Flag modules with >=5 dependents as high-impact change targets."""
    warnings: list[str] = []
    all_names = {m.name for m in spec.modules}
    dependent_count: dict[str, int] = {m.name: 0 for m in spec.modules}

    for mod in spec.modules:
        for dep in mod.depends_on:
            if dep in dependent_count:
                dependent_count[dep] += 1

    for edge in spec.dependency_edges:
        if edge.target in all_names and edge.source in all_names:
            dependent_count.setdefault(edge.target, 0)

    for name, count in dependent_count.items():
        if count >= 5:
            warnings.append(
                f"Hub module: '{name}' has {count} dependents (high-impact change target)"
            )

    return warnings


def _compute_coupling(spec: RepoSpec) -> dict[str, CouplingMetrics]:
    """Compute afferent (Ca) and efferent (Ce) coupling per module."""
    all_names = {m.name for m in spec.modules}
    metrics: dict[str, CouplingMetrics] = {m.name: CouplingMetrics() for m in spec.modules}

    for mod in spec.modules:
        metrics[mod.name].efferent = len([d for d in mod.depends_on if d in all_names])

    for mod in spec.modules:
        for dep in mod.depends_on:
            if dep in metrics:
                metrics[dep].afferent += 1

    for name, m in metrics.items():
        total = m.afferent + m.efferent
        m.instability = m.efferent / total if total > 0 else 0.0

    return metrics


def _check_behavioral_sections(spec: RepoSpec) -> list[str]:
    """Warn about missing behavioral sections in new-format specs."""
    warnings: list[str] = []

    has_behavioral = bool(spec.problem_statement or spec.domain_model_raw or spec.failure_model)
    if not has_behavioral:
        return warnings

    if not spec.domain_model_raw:
        warnings.append("Domain model section is empty — consider documenting Pydantic models")

    if not spec.failure_model:
        warnings.append(
            "Failure model section is empty — consider documenting error types and recovery"
        )

    if not spec.configuration_spec:
        warnings.append(
            "Configuration specification is empty — consider documenting config sources"
        )

    for mod in spec.modules:
        if mod.behavioral_spec and "MUST" not in mod.behavioral_spec:
            warnings.append(
                f"Module '{mod.name}' behavioral spec lacks RFC 2119 normative language"
            )

    return warnings


def _format_validation_report(result: ValidationResult, spec: RepoSpec) -> str:
    """Format validation results as human-readable Markdown."""
    lines: list[str] = ["# Spec Validation Report", ""]

    lines.append("## Summary")
    lines.append("")
    status = "PASS" if result.passed else "FAIL"
    lines.append(f"**Status:** {status}")
    lines.append(f"**Errors:** {len(result.errors)}")
    lines.append(f"**Warnings:** {len(result.warnings)}")
    lines.append(f"**Modules:** {len(spec.modules)}")
    lines.append("")

    if result.errors:
        lines.append("## Errors")
        lines.append("")
        for err in result.errors:
            lines.append(f"- {err}")
        lines.append("")

    if result.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warn in result.warnings:
            lines.append(f"- {warn}")
        lines.append("")

    if result.metrics:
        lines.append("## Coupling Metrics")
        lines.append("")
        lines.append("| Module | Ca (afferent) | Ce (efferent) | I (instability) |")
        lines.append("|--------|--------------|--------------|-----------------|")
        for name in sorted(result.metrics):
            m = result.metrics[name]
            lines.append(f"| {name} | {m.afferent} | {m.efferent} | {m.instability:.2f} |")
        lines.append("")

    return "\n".join(lines)


async def validate_spec(project_path: Path) -> ValidationResult:
    """Validate .factory/SPEC.md (or legacy GRAPH-SPEC.md) against the actual project.

    Tier 1: Structural checks (pure Python) — path existence, orphan/hub detection,
    coupling metrics (legacy specs).
    Tier 2: Import cross-referencing (Haiku) — language-agnostic verification that
    declared dependency edges match actual imports.
    Tier 3: Behavioral checks — section completeness for new-format specs.

    Writes results to .factory/spec_validation.md.
    """
    spec_path = project_path / ".factory" / "SPEC.md"
    if not spec_path.is_file():
        spec_path = project_path / ".factory" / "GRAPH-SPEC.md"
    spec = parse_spec(spec_path)

    result = ValidationResult()

    path_errors, path_warnings = _check_paths(spec, project_path)
    result.errors.extend(path_errors)
    result.warnings.extend(path_warnings)

    import_errors, import_warnings = await _check_imports_haiku(spec, project_path)
    result.errors.extend(import_errors)
    result.warnings.extend(import_warnings)

    result.warnings.extend(_detect_orphans(spec))
    result.warnings.extend(_detect_hubs(spec))
    result.warnings.extend(_check_behavioral_sections(spec))

    result.metrics = _compute_coupling(spec)

    report = _format_validation_report(result, spec)
    output_path = project_path / ".factory" / "spec_validation.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    log.info(
        "spec.validate.complete",
        errors=len(result.errors),
        warnings=len(result.warnings),
        modules=len(spec.modules),
        output=str(output_path),
    )

    return result
