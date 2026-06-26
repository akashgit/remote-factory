"""Spec impact query engine — extract module subgraph for agent context."""

from __future__ import annotations

from pathlib import Path

import structlog

from factory.spec.parser import RepoSpec, parse_spec

log = structlog.get_logger()


def _find_dependents(module_name: str, spec: RepoSpec) -> list[str]:
    """Find modules that depend on the given module."""
    lower = module_name.lower()
    dependents: list[str] = []
    for mod in spec.modules:
        if mod.name.lower() == lower:
            continue
        for dep in mod.depends_on:
            if dep.lower() == lower:
                dependents.append(mod.name)
                break
    return sorted(dependents)


def _find_contracts(module_name: str, spec: RepoSpec) -> list[str]:
    """Find shared contracts owned by the given module."""
    contracts: list[str] = []
    for contract in spec.shared_contracts:
        if contract.defined_in.lower() == module_name.lower():
            consumers = ", ".join(contract.used_by) if contract.used_by else "none"
            risk = contract.change_risk or "unknown"
            contracts.append(f"{contract.name} (used by: {consumers}, risk: {risk})")
    return contracts


def _find_change_impact(module_name: str, spec: RepoSpec) -> str | None:
    """Find change impact entry for the given module."""
    lower = module_name.lower()
    for impact in spec.change_impact:
        if impact.module.lower() == lower:
            parts = []
            if impact.classification:
                parts.append(f"classification: {impact.classification}")
            if impact.impact:
                parts.append(f"impact: {impact.impact}")
            return ", ".join(parts) if parts else None
    return None


def get_impact(module_name: str, project_path: Path) -> str:
    """Extract the subgraph centered on a named module from the repo spec.

    Returns a compact Markdown snippet sized for agent context inclusion.
    Raises ValueError if the module is not found in the spec.
    Raises FileNotFoundError if the spec file does not exist.
    """
    spec_path = project_path / ".factory" / "GRAPH-SPEC.md"
    spec = parse_spec(spec_path)

    module = spec.get_module(module_name)
    if module is None:
        raise ValueError(f"Module '{module_name}' not found in spec at {spec_path}")

    dependents = _find_dependents(module_name, spec)
    contracts = _find_contracts(module_name, spec)
    change_impact = _find_change_impact(module_name, spec)

    lines: list[str] = []
    lines.append(f"## Impact: {module.name}")
    lines.append("")
    lines.append(f"**Path:** `{module.path}`")
    if module.role:
        lines.append(f"**Role:** {module.role}")
    if module.classification:
        lines.append(f"**Classification:** {module.classification}")
    lines.append("")

    lines.append("### Dependencies (imports)")
    if module.depends_on:
        for dep in module.depends_on:
            lines.append(f"- {dep}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("### Dependents (imported by)")
    if dependents:
        for dep in dependents:
            lines.append(f"- {dep}")
    else:
        lines.append("- None")
    lines.append("")

    if contracts:
        lines.append("### Contracts Owned")
        for c in contracts:
            lines.append(f"- {c}")
        lines.append("")

    if change_impact:
        lines.append("### Change Impact")
        lines.append(f"- {change_impact}")
        lines.append("")

    log.info("spec.impact", module=module_name, dependents=len(dependents))
    return "\n".join(lines)
