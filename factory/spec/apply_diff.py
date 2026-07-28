"""Apply a SPEC Diff from strategy to SPEC.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()


@dataclass
class ModuleEntry:
    name: str
    body: str


@dataclass
class SpecDiff:
    added: list[ModuleEntry] = field(default_factory=list)
    modified: list[ModuleEntry] = field(default_factory=list)
    removed: list[ModuleEntry] = field(default_factory=list)


def extract_spec_diff(strategy_text: str) -> SpecDiff | None:
    """Extract the ## SPEC Diff section from strategy text.

    Returns None if no SPEC Diff section is found.
    """
    match = re.search(
        r"^## SPEC Diff\s*\n(.*?)(?=\n## (?!#)|\Z)",
        strategy_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return None

    section = match.group(1)
    diff = SpecDiff()

    category_pattern = re.compile(
        r"^### (ADDED|MODIFIED|REMOVED) Modules\s*\n(.*?)(?=\n### |\Z)",
        re.MULTILINE | re.DOTALL,
    )

    for cat_match in category_pattern.finditer(section):
        category = cat_match.group(1)
        content = cat_match.group(2)
        modules = _parse_module_entries(content)

        if category == "ADDED":
            diff.added = modules
        elif category == "MODIFIED":
            diff.modified = modules
        elif category == "REMOVED":
            diff.removed = modules

    return diff


def _parse_module_entries(text: str) -> list[ModuleEntry]:
    """Parse #### module `<name>` entries from a category section."""
    entries: list[ModuleEntry] = []
    pattern = re.compile(
        r"^#### module `([^`]+)`\s*\n(.*?)(?=\n#### |\Z)",
        re.MULTILINE | re.DOTALL,
    )

    for m in pattern.finditer(text):
        name = m.group(1).strip()
        body = m.group(2).strip()
        entries.append(ModuleEntry(name=name, body=body))

    return entries


def _find_module_section(spec_lines: list[str], module_name: str) -> tuple[int, int] | None:
    """Find the start and end line indices of a module section in SPEC.md.

    Looks for patterns like:
      ### module `<name>`
      ### `<name>`
      ### <name>
    """
    pattern = re.compile(
        rf"^###\s+(?:module\s+)?(?:`{re.escape(module_name)}`|{re.escape(module_name)})\s*$"
    )
    start = None
    for i, line in enumerate(spec_lines):
        if start is None:
            if pattern.match(line.strip()):
                start = i
        else:
            stripped = line.strip()
            if stripped.startswith("### ") and not stripped.startswith("#### "):
                return (start, i)

    if start is not None:
        return (start, len(spec_lines))

    return None


def _build_module_block(entry: ModuleEntry) -> str:
    """Build a markdown block for a module entry."""
    return f"### module `{entry.name}`\n\n{entry.body}\n"


def apply_spec_diff(project_path: Path, strategy_path: Path | None = None) -> bool:
    """Apply the SPEC Diff from strategy to SPEC.md.

    Args:
        project_path: Root of the target project.
        strategy_path: Path to the strategy file containing the SPEC Diff.
            Defaults to project_path / ".factory" / "strategy" / "current.md".

    Returns:
        True if changes were applied, False if no SPEC Diff section was found.
    """
    if strategy_path is None:
        strategy_path = project_path / ".factory" / "strategy" / "current.md"

    if not strategy_path.is_file():
        log.info("spec.apply_diff.skip", reason="strategy file not found", path=str(strategy_path))
        return False

    strategy_text = strategy_path.read_text(encoding="utf-8")
    diff = extract_spec_diff(strategy_text)

    if diff is None:
        log.info("spec.apply_diff.skip", reason="no SPEC Diff section found")
        return False

    if not diff.added and not diff.modified and not diff.removed:
        log.info("spec.apply_diff.skip", reason="SPEC Diff section is empty")
        return False

    spec_path = project_path / "SPEC.md"

    if spec_path.is_file():
        spec_text = spec_path.read_text(encoding="utf-8")
    else:
        log.info("spec.apply_diff.create", path=str(spec_path))
        spec_text = "# SPEC\n"

    spec_lines = spec_text.splitlines(keepends=True)

    removed_count = 0
    for entry in diff.removed:
        bounds = _find_module_section([line.rstrip("\n") for line in spec_lines], entry.name)
        if bounds:
            start, end = bounds
            del spec_lines[start:end]
            removed_count += 1
            log.debug("spec.apply_diff.removed", module=entry.name)
        else:
            log.warning("spec.apply_diff.remove_miss", module=entry.name)

    modified_count = 0
    for entry in diff.modified:
        plain_lines = [line.rstrip("\n") for line in spec_lines]
        bounds = _find_module_section(plain_lines, entry.name)
        if bounds:
            start, end = bounds
            replacement = _build_module_block(entry) + "\n"
            spec_lines[start:end] = [replacement]
            modified_count += 1
            log.debug("spec.apply_diff.modified", module=entry.name)
        else:
            log.warning(
                "spec.apply_diff.modify_miss",
                module=entry.name,
                action="appending as new section",
            )
            spec_lines.append("\n" + _build_module_block(entry) + "\n")
            modified_count += 1

    added_count = 0
    for entry in diff.added:
        block = "\n" + _build_module_block(entry) + "\n"
        spec_lines.append(block)
        added_count += 1
        log.debug("spec.apply_diff.added", module=entry.name)

    spec_path.write_text("".join(spec_lines), encoding="utf-8")

    log.info(
        "spec.apply_diff.complete",
        added=added_count,
        modified=modified_count,
        removed=removed_count,
        output=str(spec_path),
    )

    return True
