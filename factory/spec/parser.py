"""Spec Markdown parser — parse GRAPH-SPEC.md into structured models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()


@dataclass
class ProjectIdentity:
    """Section 3: Project Identity."""

    name: str = ""
    project_type: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    entry_point: str = ""


@dataclass
class ModuleSpec:
    """A single module entry from the repo spec."""

    name: str
    path: str = ""
    role: str = ""
    layer: str = ""
    classification: str = ""
    exports: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    contracts_owned: list[str] = field(default_factory=list)
    consumes: str = ""
    consumed_by: str = ""
    behavioral_spec: str = ""


@dataclass
class DependencyEdge:
    """A directed dependency edge between two modules (legacy — kept for backward compat)."""

    source: str
    target: str
    import_type: str = "direct"
    coupling: str = "strong"
    surface: str = ""


@dataclass
class SharedContract:
    """A shared contract (type, schema) used across modules."""

    name: str
    defined_in: str = ""
    type: str = ""
    consumers: list[str] = field(default_factory=list)
    change_risk: str = ""


@dataclass
class EntryPoint:
    """An external entry point into the codebase."""

    name: str
    module: str = ""
    type: str = ""


@dataclass
class ChangeImpact:
    """Change impact entry for a module (legacy — kept for backward compat)."""

    module: str
    affects: list[str] = field(default_factory=list)
    reason: str = ""
    severity: str = ""


@dataclass
class CouplingMetricEntry:
    """Coupling metrics for a module (legacy — kept for backward compat)."""

    module: str
    ca: int = 0
    ce: int = 0
    instability: float = 0.0
    classification: str = ""


@dataclass
class RepoSpec:
    """Parsed representation of GRAPH-SPEC.md."""

    identity: ProjectIdentity = field(default_factory=ProjectIdentity)
    goals: str = ""
    technical_stack: str = ""
    abstraction_levels: list[str] = field(default_factory=list)
    modules: list[ModuleSpec] = field(default_factory=list)
    dependency_edges: list[DependencyEdge] = field(default_factory=list)
    shared_contracts: list[SharedContract] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)
    change_impact: list[ChangeImpact] = field(default_factory=list)
    coupling_metrics: list[CouplingMetricEntry] = field(default_factory=list)
    problem_statement: str = ""
    non_goals: str = ""
    design_philosophy: str = ""
    data_flow_summary: str = ""
    domain_model_raw: str = ""
    state_machines_raw: str = ""
    configuration_spec: str = ""
    failure_model: str = ""
    security: str = ""
    test_matrix: str = ""
    extension_points: str = ""
    implementation_checklist: str = ""

    def get_module(self, name: str) -> ModuleSpec | None:
        """Look up a module by name (case-insensitive)."""
        lower = name.lower()
        for m in self.modules:
            if m.name.lower() == lower:
                return m
        return None


def _parse_comma_list(text: str) -> list[str]:
    """Split a comma-separated string into a trimmed list, filtering empties."""
    if not text or text.strip().lower() in ("none", "—", "-", "n/a", ""):
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _extract_field(block: str, field_name: str) -> str:
    """Extract a **Field:** value from a markdown block."""
    m = re.search(rf"\*\*{re.escape(field_name)}:\*\*\s*(.+)", block)
    return m.group(1).strip().strip("`") if m else ""


def _extract_section(content: str, heading_pattern: str) -> str:
    """Extract content between a heading matching the pattern and the next same-or-higher-level heading."""
    heading_re = re.compile(rf"^(#{{1,4}})\s+{heading_pattern}", re.MULTILINE)
    match = heading_re.search(content)
    if not match:
        return ""

    level = len(match.group(1))
    start = match.end()
    next_heading = re.compile(rf"^#{{{1},{level}}}\s+", re.MULTILINE)
    next_match = next_heading.search(content[start:])
    return content[start : start + next_match.start()] if next_match else content[start:]


def _extract_section_by_title(content: str, title: str) -> str:
    """Extract content for a section matched by title text (ignoring section numbers)."""
    escaped = re.escape(title)
    pattern = rf"(?:\d+(?:\.\d+)*\.?\s+)?{escaped}"
    return _extract_section(content, pattern)


# ── Identity / Goals / Levels (match by title text) ──────────────


def _parse_identity(content: str) -> ProjectIdentity:
    """Parse Project Identity section."""
    section = _extract_section_by_title(content, "Project Identity")
    if not section:
        return ProjectIdentity()

    return ProjectIdentity(
        name=_extract_field(section, "Name"),
        project_type=_extract_field(section, "Type"),
        language=_extract_field(section, "Language"),
        framework=_extract_field(section, "Framework"),
        package_manager=_extract_field(section, "Package Manager"),
        entry_point=_extract_field(section, "Entry Point"),
    )


def _parse_goals(content: str) -> str:
    """Parse Goals section (matches 'Goals' with or without 'and Non-Goals')."""
    section = _extract_section_by_title(content, "Goals")
    if not section:
        section = _extract_section(content, r"(?:#+ *)?2\.\s+Goals")
    return section.strip() if section else ""


def _parse_abstraction_levels(content: str) -> list[str]:
    """Parse Abstraction Levels section."""
    section = _extract_section_by_title(content, "Abstraction Levels")
    if not section:
        return []
    levels: list[str] = []
    for line in section.strip().splitlines():
        stripped = line.strip()
        m = re.match(r"^\d+\.\s+\*\*(.+?)\*\*", stripped)
        if m:
            levels.append(m.group(1).strip())
    return levels


# ── Module parsing ───────────────────────────────────────────────


def _parse_modules(content: str) -> list[ModuleSpec]:
    """Parse module entries from Module Graph, Module Specifications, or Modules sections.

    Supports both old format (## Modules / ### name) and new behavioral format
    (## 8. Module Specifications / ### 8.N module-path).
    """
    modules: list[ModuleSpec] = []

    module_section = _extract_section_by_title(content, "Module Specifications")
    if not module_section:
        module_section = _extract_section(content, r"(?:4\.2\s+)?Module Graph")
    if not module_section:
        module_section = _extract_section(content, "Modules")
    if not module_section:
        return []

    module_pattern = re.compile(r"^#{3,4}\s+(?:\d+(?:\.\d+)*\s+)?(.+)$", re.MULTILINE)
    matches = list(module_pattern.finditer(module_section))

    for i, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(module_section)
        block = module_section[start:end]

        mod = ModuleSpec(
            name=name,
            path=_extract_field(block, "Path"),
            role=_extract_field(block, "Role"),
            layer=_extract_field(block, "Layer"),
            classification=_extract_field(block, "Classification"),
            exports=_parse_comma_list(_extract_field(block, "Exports")),
            depends_on=_parse_comma_list(_extract_field(block, "Depends on")),
            contracts_owned=_parse_comma_list(_extract_field(block, "Contracts owned")),
            consumes=_extract_field(block, "Consumes"),
            consumed_by=_extract_field(block, "Consumed by"),
        )

        behavioral_lines: list[str] = []
        in_behavioral = False
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("- **") or stripped.startswith("**"):
                in_behavioral = False
                continue
            if in_behavioral or (stripped and not stripped.startswith("|")):
                if stripped:
                    in_behavioral = True
                    behavioral_lines.append(stripped)

        mod.behavioral_spec = "\n".join(behavioral_lines).strip()
        modules.append(mod)

    return modules


# ── Table parsing (for legacy format) ────────────────────────────


def _parse_table_rows(content: str, section_header: str) -> list[list[str]]:
    """Extract rows from a Markdown table under a given section header."""
    pattern = re.compile(
        rf"^#{{2,4}}\s+(?:\d+(?:\.\d+)*\.?\s+)?{re.escape(section_header)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return []

    level = content[match.start() : match.end()].count("#", 0, 5)
    start = match.end()
    next_section = re.compile(rf"^#{{{1},{level}}}\s+", re.MULTILINE)
    ns = next_section.search(content[start:])
    table_block = content[start : start + ns.start()] if ns else content[start:]

    rows: list[list[str]] = []
    in_table = False
    separator_seen = False

    for line in table_block.strip().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue

        in_table = True
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            separator_seen = True
            continue

        if not separator_seen:
            continue

        cells = [c.strip() for c in stripped.split("|")]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)

    return rows


def _parse_dependency_edges(content: str) -> list[DependencyEdge]:
    """Parse the Dependency Edges table (legacy format — returns empty for new specs)."""
    rows = _parse_table_rows(content, "Dependency Edges")
    edges: list[DependencyEdge] = []
    for row in rows:
        if len(row) >= 2:
            edge = DependencyEdge(
                source=row[0],
                target=row[1],
                import_type=row[2] if len(row) > 2 else "direct",
                coupling=row[3] if len(row) > 3 else "strong",
                surface=row[4] if len(row) > 4 else "",
            )
            edges.append(edge)
    return edges


def _parse_shared_contracts(content: str) -> list[SharedContract]:
    """Parse the Shared Contracts section.

    Supports both table format and subsection format (#### ContractName blocks).
    """
    section = _extract_section_by_title(content, "Shared Contracts")
    if not section:
        return []

    contracts: list[SharedContract] = []

    sub_pattern = re.compile(r"^#{3,5}\s+(?:\d+(?:\.\d+)*\s+)?(.+)$", re.MULTILINE)
    matches = list(sub_pattern.finditer(section))
    if matches:
        for i, match in enumerate(matches):
            name = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
            block = section[start:end]

            contract = SharedContract(
                name=name,
                defined_in=_extract_field(block, "Defined in"),
                type=_extract_field(block, "Type"),
                consumers=_parse_comma_list(_extract_field(block, "Consumers")),
                change_risk=_extract_field(block, "Change Risk"),
            )
            contracts.append(contract)
        return contracts

    rows = _parse_table_rows(content, "Shared Contracts")
    for row in rows:
        if len(row) >= 2:
            contract = SharedContract(
                name=row[0],
                defined_in=row[1] if len(row) > 1 else "",
                consumers=_parse_comma_list(row[2]) if len(row) > 2 else [],
                change_risk=row[3] if len(row) > 3 else "",
            )
            contracts.append(contract)
    return contracts


def _parse_entry_points(content: str) -> list[EntryPoint]:
    """Parse the Entry Points table."""
    rows = _parse_table_rows(content, "Entry Points")
    points: list[EntryPoint] = []
    for row in rows:
        if len(row) >= 1:
            point = EntryPoint(
                name=row[0],
                module=row[1] if len(row) > 1 else "",
                type=row[2] if len(row) > 2 else "",
            )
            points.append(point)
    return points


def _parse_change_impact(content: str) -> list[ChangeImpact]:
    """Parse the Change Impact table (legacy format — returns empty for new specs)."""
    rows = _parse_table_rows(content, "Change Impact")
    impacts: list[ChangeImpact] = []
    for row in rows:
        if len(row) >= 1:
            impact = ChangeImpact(
                module=row[0],
                affects=_parse_comma_list(row[1]) if len(row) > 1 else [],
                reason=row[2] if len(row) > 2 else "",
                severity=row[3] if len(row) > 3 else "",
            )
            impacts.append(impact)
    return impacts


def _parse_coupling_metrics(content: str) -> list[CouplingMetricEntry]:
    """Parse the Coupling Metrics table (legacy format — returns empty for new specs)."""
    rows = _parse_table_rows(content, "Coupling Metrics")
    metrics: list[CouplingMetricEntry] = []
    for row in rows:
        if len(row) >= 1:
            try:
                ca = int(row[1]) if len(row) > 1 else 0
                ce = int(row[2]) if len(row) > 2 else 0
                instability = float(row[3]) if len(row) > 3 else 0.0
            except (ValueError, IndexError):
                ca, ce, instability = 0, 0, 0.0

            entry = CouplingMetricEntry(
                module=row[0],
                ca=ca,
                ce=ce,
                instability=instability,
                classification=row[4] if len(row) > 4 else "",
            )
            metrics.append(entry)
    return metrics


# ── New behavioral section parsers ───────────────────────────────


def _parse_problem_statement(content: str) -> str:
    """Parse §1 Problem Statement."""
    return _extract_section_by_title(content, "Problem Statement").strip()


def _parse_non_goals(content: str) -> str:
    """Parse §2.2 Non-Goals."""
    section = _extract_section_by_title(content, "Non-Goals")
    return section.strip() if section else ""


def _parse_design_philosophy(content: str) -> str:
    """Parse §2.3 Design Philosophy."""
    section = _extract_section_by_title(content, "Design Philosophy")
    return section.strip() if section else ""


def _parse_data_flow_summary(content: str) -> str:
    """Parse §5.2 Data Flow Summary."""
    section = _extract_section_by_title(content, "Data Flow Summary")
    return section.strip() if section else ""


def _parse_domain_model(content: str) -> str:
    """Parse §6 Domain Model."""
    section = _extract_section_by_title(content, "Domain Model")
    return section.strip() if section else ""


def _parse_state_machines(content: str) -> str:
    """Parse §7 State Machines and Lifecycles."""
    section = _extract_section_by_title(content, "State Machines")
    return section.strip() if section else ""


def _parse_configuration(content: str) -> str:
    """Parse §10 Configuration Specification."""
    section = _extract_section_by_title(content, "Configuration Specification")
    return section.strip() if section else ""


def _parse_failure_model(content: str) -> str:
    """Parse §12 Failure Model and Recovery."""
    section = _extract_section_by_title(content, "Failure Model")
    return section.strip() if section else ""


def _parse_security(content: str) -> str:
    """Parse §13 Security and Safety."""
    section = _extract_section_by_title(content, "Security and Safety")
    return section.strip() if section else ""


def _parse_test_matrix(content: str) -> str:
    """Parse §14 Test and Validation Matrix."""
    section = _extract_section_by_title(content, "Test and Validation Matrix")
    return section.strip() if section else ""


def _parse_extension_points(content: str) -> str:
    """Parse §15 Extension Points."""
    section = _extract_section_by_title(content, "Extension Points")
    return section.strip() if section else ""


def _parse_checklist(content: str) -> str:
    """Parse §16 Implementation Checklist."""
    section = _extract_section_by_title(content, "Implementation Checklist")
    return section.strip() if section else ""


# ── Main entry point ─────────────────────────────────────────────


def parse_spec(spec_path: Path) -> RepoSpec:
    """Parse GRAPH-SPEC.md into a structured RepoSpec model.

    Supports both the new behavioral format and the legacy structural format.
    Raises FileNotFoundError if the spec file does not exist.
    """
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    content = spec_path.read_text()
    log.info("spec.parse", path=str(spec_path), chars=len(content))

    spec = RepoSpec(
        identity=_parse_identity(content),
        goals=_parse_goals(content),
        abstraction_levels=_parse_abstraction_levels(content),
        modules=_parse_modules(content),
        dependency_edges=_parse_dependency_edges(content),
        shared_contracts=_parse_shared_contracts(content),
        entry_points=_parse_entry_points(content),
        change_impact=_parse_change_impact(content),
        coupling_metrics=_parse_coupling_metrics(content),
        problem_statement=_parse_problem_statement(content),
        non_goals=_parse_non_goals(content),
        design_philosophy=_parse_design_philosophy(content),
        data_flow_summary=_parse_data_flow_summary(content),
        domain_model_raw=_parse_domain_model(content),
        state_machines_raw=_parse_state_machines(content),
        configuration_spec=_parse_configuration(content),
        failure_model=_parse_failure_model(content),
        security=_parse_security(content),
        test_matrix=_parse_test_matrix(content),
        extension_points=_parse_extension_points(content),
        implementation_checklist=_parse_checklist(content),
    )

    log.info(
        "spec.parsed",
        modules=len(spec.modules),
        edges=len(spec.dependency_edges),
        contracts=len(spec.shared_contracts),
        entry_points=len(spec.entry_points),
    )
    return spec
