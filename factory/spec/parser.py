"""Spec Markdown parser — parse .factory/GRAPH-SPEC.md into structured models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()


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


@dataclass
class DependencyEdge:
    """A directed dependency edge between two modules."""

    source: str
    target: str
    import_type: str = "direct"
    coupling: str = "strong"


@dataclass
class SharedContract:
    """A shared contract (type, schema) used across modules."""

    name: str
    defined_in: str = ""
    used_by: list[str] = field(default_factory=list)
    change_risk: str = ""


@dataclass
class EntryPoint:
    """An external entry point into the codebase."""

    name: str
    module: str = ""
    type: str = ""


@dataclass
class ChangeImpact:
    """Change impact entry for a module."""

    module: str
    classification: str = ""
    dependents: list[str] = field(default_factory=list)
    impact: str = ""


@dataclass
class RepoSpec:
    """Parsed representation of .factory/GRAPH-SPEC.md."""

    modules: list[ModuleSpec] = field(default_factory=list)
    dependency_edges: list[DependencyEdge] = field(default_factory=list)
    shared_contracts: list[SharedContract] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)
    change_impact: list[ChangeImpact] = field(default_factory=list)

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


def _parse_modules(content: str) -> list[ModuleSpec]:
    """Parse the ## Modules section into ModuleSpec objects."""
    modules: list[ModuleSpec] = []

    module_pattern = re.compile(r"^###\s+(.+)$", re.MULTILINE)
    matches = list(module_pattern.finditer(content))

    for i, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        block = content[start:end]

        mod = ModuleSpec(name=name)

        path_m = re.search(r"\*\*Path:\*\*\s*(.+)", block)
        if path_m:
            mod.path = path_m.group(1).strip().strip("`")

        role_m = re.search(r"\*\*Role:\*\*\s*(.+)", block)
        if role_m:
            mod.role = role_m.group(1).strip()

        layer_m = re.search(r"\*\*Layer:\*\*\s*(.+)", block)
        if layer_m:
            mod.layer = layer_m.group(1).strip()

        classification_m = re.search(r"\*\*Classification:\*\*\s*(.+)", block)
        if classification_m:
            mod.classification = classification_m.group(1).strip()

        exports_m = re.search(r"\*\*Exports:\*\*\s*(.+)", block)
        if exports_m:
            mod.exports = _parse_comma_list(exports_m.group(1).strip().strip("`"))

        depends_m = re.search(r"\*\*Depends on:\*\*\s*(.+)", block)
        if depends_m:
            mod.depends_on = _parse_comma_list(depends_m.group(1).strip().strip("`"))

        contracts_m = re.search(r"\*\*Contracts owned:\*\*\s*(.+)", block)
        if contracts_m:
            mod.contracts_owned = _parse_comma_list(contracts_m.group(1).strip().strip("`"))

        modules.append(mod)

    return modules


def _parse_table_rows(content: str, section_header: str) -> list[list[str]]:
    """Extract rows from a Markdown table under a given ## section header.

    Returns a list of rows, where each row is a list of cell values.
    Skips the header row and separator row.
    """
    pattern = re.compile(
        rf"^##\s+{re.escape(section_header)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return []

    start = match.end()
    next_section = re.search(r"^##\s+", content[start:], re.MULTILINE)
    table_block = content[start : start + next_section.start()] if next_section else content[start:]

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
    """Parse the ## Dependency Edges table."""
    rows = _parse_table_rows(content, "Dependency Edges")
    edges: list[DependencyEdge] = []
    for row in rows:
        if len(row) >= 2:
            edge = DependencyEdge(
                source=row[0],
                target=row[1],
                import_type=row[2] if len(row) > 2 else "direct",
                coupling=row[3] if len(row) > 3 else "strong",
            )
            edges.append(edge)
    return edges


def _parse_shared_contracts(content: str) -> list[SharedContract]:
    """Parse the ## Shared Contracts table."""
    rows = _parse_table_rows(content, "Shared Contracts")
    contracts: list[SharedContract] = []
    for row in rows:
        if len(row) >= 2:
            contract = SharedContract(
                name=row[0],
                defined_in=row[1] if len(row) > 1 else "",
                used_by=_parse_comma_list(row[2]) if len(row) > 2 else [],
                change_risk=row[3] if len(row) > 3 else "",
            )
            contracts.append(contract)
    return contracts


def _parse_entry_points(content: str) -> list[EntryPoint]:
    """Parse the ## Entry Points table."""
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
    """Parse the ## Change Impact table."""
    rows = _parse_table_rows(content, "Change Impact")
    impacts: list[ChangeImpact] = []
    for row in rows:
        if len(row) >= 1:
            impact = ChangeImpact(
                module=row[0],
                classification=row[1] if len(row) > 1 else "",
                dependents=_parse_comma_list(row[2]) if len(row) > 2 else [],
                impact=row[3] if len(row) > 3 else "",
            )
            impacts.append(impact)
    return impacts


def parse_spec(spec_path: Path) -> RepoSpec:
    """Parse .factory/GRAPH-SPEC.md into a structured RepoSpec model.

    Raises FileNotFoundError if the spec file does not exist.
    """
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    content = spec_path.read_text()
    log.info("spec.parse", path=str(spec_path), chars=len(content))

    modules_section = ""
    modules_match = re.search(r"^##\s+Modules\s*$", content, re.MULTILINE)
    if modules_match:
        start = modules_match.end()
        next_h2 = re.search(r"^##\s+(?!#)", content[start:], re.MULTILINE)
        modules_section = content[start : start + next_h2.start()] if next_h2 else content[start:]

    spec = RepoSpec(
        modules=_parse_modules(modules_section),
        dependency_edges=_parse_dependency_edges(content),
        shared_contracts=_parse_shared_contracts(content),
        entry_points=_parse_entry_points(content),
        change_impact=_parse_change_impact(content),
    )

    log.info(
        "spec.parsed",
        modules=len(spec.modules),
        edges=len(spec.dependency_edges),
        contracts=len(spec.shared_contracts),
        entry_points=len(spec.entry_points),
    )
    return spec
