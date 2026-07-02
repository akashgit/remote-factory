"""Spec update — diff scoping via Haiku agent and incremental spec patching."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.spec._json_util import extract_json

log = structlog.get_logger()

SCOPE_PROMPT = """\
Analyze this git diff against the repo spec and identify which spec modules are affected.

## GRAPH-SPEC.md
{spec_content}

## Git Diff
{diff_text}

## Output
Return a JSON object:
{{
  "affected_modules": ["<module name>", ...],
  "new_files": ["<file path>", ...],
  "deleted_files": ["<file path>", ...]
}}
- affected_modules: module names from the spec whose declared path covers a changed file
- new_files: changed files that don't map to any existing module
- deleted_files: files removed in this diff
Return ONLY the JSON object.
"""


@dataclass
class DiffScope:
    """Result of scoping a diff against the existing repo spec."""

    affected_modules: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)


def _get_diff_text(project_path: Path, experiment_id: int | None, spec_rel: str) -> str:
    """Get diff text from an experiment file or git."""
    if experiment_id is not None:
        diff_path = project_path / ".factory" / "experiments" / str(experiment_id) / "changes.diff"
        if not diff_path.is_file():
            raise FileNotFoundError(f"No diff found at {diff_path}")
        return diff_path.read_text()

    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", spec_rel],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    spec_commit = result.stdout.strip() if result.returncode == 0 else ""
    base_ref = spec_commit or "HEAD~1"

    result = subprocess.run(
        ["git", "diff", base_ref, "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr[:200]}")
    return result.stdout


async def scope_diff(project_path: Path, experiment_id: int | None = None) -> DiffScope:
    """Scope a diff against the existing repo spec using a Haiku agent call.

    If experiment_id is provided, reads .factory/experiments/{id}/changes.diff.
    Otherwise, diffs between HEAD and the commit that last touched GRAPH-SPEC.md.
    """
    from factory.agents.runner import invoke_agent
    from factory.spec import read_spec

    spec_content = read_spec(project_path)

    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    spec_rel = str(spec_path.relative_to(project_path)) if spec_path else "GRAPH-SPEC.md"

    diff_text = _get_diff_text(project_path, experiment_id, spec_rel)

    prompt = SCOPE_PROMPT.format(spec_content=spec_content, diff_text=diff_text)

    result_text, code = await invoke_agent(
        "researcher",
        prompt,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        raise RuntimeError(f"Scope diff agent failed (exit {code})")

    data = extract_json(result_text)
    if not isinstance(data, dict):
        raise RuntimeError("Scope diff agent returned non-object JSON")

    scope = DiffScope(
        affected_modules=sorted(data.get("affected_modules", [])),
        new_files=sorted(data.get("new_files", [])),
        deleted_files=sorted(data.get("deleted_files", [])),
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

    1. Scope the diff
    2. Run patcher agent to update GRAPH-SPEC.md
    3. Re-validate

    Returns the path to the updated GRAPH-SPEC.md.
    """
    from factory.agents.runner import invoke_agent
    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")

    scope = await scope_diff(project_path)

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
