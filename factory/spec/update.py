"""Spec update — diff scoping and incremental spec patching after kept experiments."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.spec.parser import parse_spec

log = structlog.get_logger()


@dataclass
class DiffScope:
    """Result of scoping a diff against the existing repo spec."""

    affected_modules: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)


def _parse_diff_files(diff_text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract added, modified, and deleted file paths from a unified diff.

    Returns (modified_files, added_files, deleted_files).
    """
    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []

    i = 0
    lines = diff_text.splitlines()
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git"):
            a_path = ""
            b_path = ""
            parts = line.split()
            for p in parts:
                if p.startswith("a/"):
                    a_path = p[2:]
                elif p.startswith("b/"):
                    b_path = p[2:]

            is_new = False
            is_deleted = False
            j = i + 1
            while j < len(lines) and not lines[j].startswith("diff --git"):
                if lines[j].startswith("new file"):
                    is_new = True
                elif lines[j].startswith("deleted file"):
                    is_deleted = True
                j += 1

            file_path = b_path or a_path
            if is_new:
                added.append(file_path)
            elif is_deleted:
                deleted.append(a_path or file_path)
            else:
                modified.append(file_path)

            i = j
        else:
            i += 1

    return modified, added, deleted


def _map_file_to_module(file_path: str, modules: list[dict[str, str]]) -> str | None:
    """Map a file path to the module that owns it based on module paths."""
    best_match: str | None = None
    best_len = 0

    for mod in modules:
        mod_path = mod["path"]
        if not mod_path:
            continue
        if file_path == mod_path or file_path.startswith(mod_path.rstrip("/") + "/"):
            if len(mod_path) > best_len:
                best_match = mod["name"]
                best_len = len(mod_path)

    return best_match


def scope_diff(project_path: Path, experiment_id: int | None = None) -> DiffScope:
    """Scope a diff against the existing repo spec.

    If experiment_id is provided, reads .factory/experiments/{id}/changes.diff.
    Otherwise, diffs between HEAD and the commit that last touched GRAPH-SPEC.md.
    """
    spec_path = project_path / ".factory" / "GRAPH-SPEC.md"
    if not spec_path.is_file():
        raise FileNotFoundError(f"No repo spec found at {spec_path}")

    spec = parse_spec(spec_path)

    if experiment_id is not None:
        diff_path = project_path / ".factory" / "experiments" / str(experiment_id) / "changes.diff"
        if not diff_path.is_file():
            raise FileNotFoundError(f"No diff found at {diff_path}")
        diff_text = diff_path.read_text()
    else:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", ".factory/GRAPH-SPEC.md"],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        spec_commit = result.stdout.strip() if result.returncode == 0 else ""
        base_ref = spec_commit or "HEAD~1"

        result = subprocess.run(
            ["git", "diff", base_ref, "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr[:200]}")
        diff_text = result.stdout

    modified, added, deleted = _parse_diff_files(diff_text)

    module_lookup = [{"name": m.name, "path": m.path} for m in spec.modules]

    affected: set[str] = set()
    new_files: list[str] = []

    for f in modified:
        mod_name = _map_file_to_module(f, module_lookup)
        if mod_name:
            affected.add(mod_name)
        else:
            new_files.append(f)

    for f in added:
        mod_name = _map_file_to_module(f, module_lookup)
        if mod_name:
            affected.add(mod_name)
        else:
            new_files.append(f)

    for f in deleted:
        mod_name = _map_file_to_module(f, module_lookup)
        if mod_name:
            affected.add(mod_name)

    scope = DiffScope(
        affected_modules=sorted(affected),
        new_files=sorted(new_files),
        deleted_files=sorted(deleted),
    )

    scope_path = project_path / ".factory" / "spec_update_scope.md"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(_format_scope(scope))

    log.info(
        "spec.scope_diff",
        affected=len(scope.affected_modules),
        new_files=len(scope.new_files),
        deleted=len(scope.deleted_files),
    )

    return scope


def _format_scope(scope: DiffScope) -> str:
    """Format a DiffScope as human-readable Markdown."""
    lines = ["# Spec Update Scope", ""]

    lines.append("## Affected Modules")
    lines.append("")
    if scope.affected_modules:
        for mod in scope.affected_modules:
            lines.append(f"- {mod}")
    else:
        lines.append("None")
    lines.append("")

    lines.append("## New Files (unmapped)")
    lines.append("")
    if scope.new_files:
        for f in scope.new_files:
            lines.append(f"- {f}")
    else:
        lines.append("None")
    lines.append("")

    lines.append("## Deleted Files")
    lines.append("")
    if scope.deleted_files:
        for f in scope.deleted_files:
            lines.append(f"- {f}")
    else:
        lines.append("None")
    lines.append("")

    return "\n".join(lines)


async def update_spec(project_path: Path) -> Path:
    """Update the repo spec based on changes since last spec commit.

    Orchestration entry point for manual trigger:
    1. Scope the diff
    2. Run patcher agent to update GRAPH-SPEC.md
    3. Re-validate

    Returns the path to the updated GRAPH-SPEC.md.
    """
    from factory.agents.runner import invoke_agent

    spec_path = project_path / ".factory" / "GRAPH-SPEC.md"
    if not spec_path.is_file():
        raise FileNotFoundError(f"No repo spec found at {spec_path}")

    scope = scope_diff(project_path)

    if not scope.affected_modules and not scope.new_files and not scope.deleted_files:
        log.info("spec.update.noop", reason="no changes detected")
        return spec_path

    patch_task = (
        f"Update the repo spec at {spec_path} based on the scoped changes.\n\n"
        f"Read the spec update scope at {project_path / '.factory' / 'spec_update_scope.md'}.\n"
        f"Read the existing spec at {spec_path}.\n"
        f"Read the changed source files to understand what changed.\n"
        f"Update affected module entries and add/remove modules as needed.\n"
        f"Write the updated spec to {spec_path}."
    )

    result, code = await invoke_agent(
        "researcher",
        patch_task,
        project_path,
        timeout=300.0,
        dangerously_skip_permissions=True,
        model="opus",
    )
    if code != 0:
        raise RuntimeError(f"Spec patch failed (exit {code}): {result[:500]}")

    log.info("spec.update.complete", output=str(spec_path))
    return spec_path
