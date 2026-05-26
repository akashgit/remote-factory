"""Architecture drift detector — compares documented structure against actual code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.miro.analyzer import ProjectStructure

log = structlog.get_logger()


@dataclass
class DocumentedComponent:
    """A module, file, or class referenced in architecture documentation."""

    name: str
    source_file: str  # which doc file referenced it
    kind: str = "module"  # module | class | file


@dataclass
class DocumentedStructure:
    """Aggregated architecture expectations parsed from project docs."""

    components: list[DocumentedComponent] = field(default_factory=list)


@dataclass
class DriftItem:
    """A single architecture drift finding."""

    category: str  # 'undocumented' | 'phantom' | 'drifted'
    name: str
    description: str
    source: str = ""  # file that evidences the drift


# ── regex patterns for extracting references from docs ──────────

# Matches Python-style module paths: factory/foo.py, factory/bar/baz.py
_FILE_REF = re.compile(r"`([\w/]+\.py)`")
# Matches module dotted paths: factory.foo, factory.bar.baz
_MODULE_REF = re.compile(r"`((?:[\w]+\.)+[\w]+)`")
# Matches class-like references: `FooBar`, `MyClass`
_CLASS_REF = re.compile(r"`([A-Z][a-zA-Z0-9]+)`")


def _extract_refs_from_text(
    text: str, source_file: str,
) -> list[DocumentedComponent]:
    """Extract module/file/class references from markdown text."""
    components: list[DocumentedComponent] = []
    seen: set[str] = set()

    for match in _FILE_REF.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            components.append(DocumentedComponent(
                name=name, source_file=source_file, kind="file",
            ))

    for match in _MODULE_REF.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            components.append(DocumentedComponent(
                name=name, source_file=source_file, kind="module",
            ))

    for match in _CLASS_REF.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            components.append(DocumentedComponent(
                name=name, source_file=source_file, kind="class",
            ))

    return components


def _extract_architecture_section(text: str) -> str:
    """Extract the '## Architecture' section from markdown text."""
    lines = text.split("\n")
    in_section = False
    section_lines: list[str] = []

    for line in lines:
        if re.match(r"^##\s+Architecture", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^##\s+", line):
            break
        if in_section:
            section_lines.append(line)

    return "\n".join(section_lines)


def parse_architecture_docs(project_path: Path) -> DocumentedStructure:
    """Parse architecture documentation from CLAUDE.md, README.md, and .factory/archive/."""
    structure = DocumentedStructure()

    # Parse CLAUDE.md architecture section
    claude_md = project_path / "CLAUDE.md"
    if claude_md.exists():
        try:
            text = claude_md.read_text(encoding="utf-8", errors="replace")
            arch_section = _extract_architecture_section(text)
            if arch_section:
                structure.components.extend(
                    _extract_refs_from_text(arch_section, "CLAUDE.md"),
                )
                log.debug("drift_parsed_claude_md", refs=len(structure.components))
        except OSError:
            log.debug("drift_claude_md_read_failed")

    # Parse README.md for architecture info
    readme = project_path / "README.md"
    if readme.exists():
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
            arch_section = _extract_architecture_section(text)
            if arch_section:
                structure.components.extend(
                    _extract_refs_from_text(arch_section, "README.md"),
                )
        except OSError:
            log.debug("drift_readme_read_failed")

    # Parse .factory/archive/decisions/ and patterns/
    for subdir in ("decisions", "patterns"):
        archive_dir = project_path / ".factory" / "archive" / subdir
        if not archive_dir.is_dir():
            continue
        for md_file in sorted(archive_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
                rel = str(md_file.relative_to(project_path))
                structure.components.extend(_extract_refs_from_text(text, rel))
            except OSError:
                continue

    log.info("drift_docs_parsed", total_refs=len(structure.components))
    return structure


def _normalize_path(name: str) -> str:
    """Normalize a dotted module path to a file path for comparison."""
    return name.replace(".", "/")


def detect(structure: ProjectStructure, project_path: Path) -> list[DriftItem]:
    """Compare analyzer's ProjectStructure against documented architecture.

    Returns a list of DriftItem findings in three categories:
      - undocumented: exists in code but not in docs
      - phantom: exists in docs but not in code
      - drifted: structural mismatch (wrong location, renamed)
    """
    documented = parse_architecture_docs(project_path)
    items: list[DriftItem] = []

    # Build sets of actual code artifacts
    actual_files: set[str] = {m.path for m in structure.modules}
    actual_classes: set[str] = set()
    for mod in structure.modules:
        for cls in mod.classes:
            actual_classes.add(cls.name)

    # Build sets of documented references by kind
    doc_files: set[str] = set()
    doc_modules: set[str] = set()
    doc_classes: set[str] = set()
    doc_sources: dict[str, str] = {}  # name -> source_file

    for comp in documented.components:
        doc_sources[comp.name] = comp.source_file
        if comp.kind == "file":
            doc_files.add(comp.name)
        elif comp.kind == "module":
            doc_modules.add(comp.name)
        elif comp.kind == "class":
            doc_classes.add(comp.name)

    # Combine file and module refs for path-based comparison
    doc_paths: set[str] = set(doc_files)
    for mod_name in doc_modules:
        doc_paths.add(_normalize_path(mod_name) + ".py")
        doc_paths.add(_normalize_path(mod_name))

    # Undocumented: in code but not in docs
    for file_path in sorted(actual_files):
        found = False
        for dp in doc_paths:
            if file_path == dp or file_path.endswith("/" + dp) or dp.endswith("/" + file_path):
                found = True
                break
        if not found and not any(
            file_path == dp or dp in file_path or file_path in dp
            for dp in doc_paths
        ):
            items.append(DriftItem(
                category="undocumented",
                name=file_path,
                description=f"Module {file_path} exists in code but is not referenced in architecture docs",
                source=file_path,
            ))

    for cls_name in sorted(actual_classes):
        if cls_name not in doc_classes:
            items.append(DriftItem(
                category="undocumented",
                name=cls_name,
                description=f"Class {cls_name} exists in code but is not referenced in architecture docs",
                source=cls_name,
            ))

    # Phantom: in docs but not in code
    for file_ref in sorted(doc_files):
        if file_ref not in actual_files and not any(
            f.endswith("/" + file_ref) or f == file_ref for f in actual_files
        ):
            items.append(DriftItem(
                category="phantom",
                name=file_ref,
                description=f"File {file_ref} referenced in {doc_sources[file_ref]} but not found in code",
                source=doc_sources[file_ref],
            ))

    for cls_ref in sorted(doc_classes):
        if cls_ref not in actual_classes:
            # Check if it's a drifted class (similar name exists)
            similar = [c for c in actual_classes if c.lower() == cls_ref.lower()]
            if similar:
                items.append(DriftItem(
                    category="drifted",
                    name=cls_ref,
                    description=(
                        f"Class {cls_ref} in {doc_sources[cls_ref]} may have been "
                        f"renamed to {similar[0]} (case mismatch)"
                    ),
                    source=doc_sources[cls_ref],
                ))
            else:
                items.append(DriftItem(
                    category="phantom",
                    name=cls_ref,
                    description=f"Class {cls_ref} referenced in {doc_sources[cls_ref]} but not found in code",
                    source=doc_sources[cls_ref],
                ))

    # Drifted: module path mismatches (documented in one location, found in another)
    for mod_name in sorted(doc_modules):
        expected_path = _normalize_path(mod_name) + ".py"
        if expected_path not in actual_files:
            # Check if the module exists under a different path
            base = expected_path.rsplit("/", 1)[-1]
            matches = [f for f in actual_files if f.endswith("/" + base) or f == base]
            if matches:
                items.append(DriftItem(
                    category="drifted",
                    name=mod_name,
                    description=(
                        f"Module {mod_name} documented as {expected_path} "
                        f"but found at {matches[0]}"
                    ),
                    source=doc_sources[mod_name],
                ))

    log.info("drift_detection_complete", total_items=len(items))
    return items
