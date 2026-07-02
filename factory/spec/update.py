"""Spec update — diff scoping via Haiku agent and incremental spec patching."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()

SCOPE_PROMPT = """\
Analyze this git diff against the repo spec and identify which spec modules are affected.

## GRAPH-SPEC.md
{spec_content}

## Git Diff
{diff_text}

## Output
Write a Markdown summary of the affected scope:
- Which existing spec modules are affected by the diff (list module names)
- Which changed files don't map to any existing module (new/unmapped files)
- Which files were deleted in this diff

Use clear headings: "## Affected Modules", "## New Files", "## Deleted Files".
List items as bullet points under each heading, or write "None" if empty.
"""


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

    rev_check = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if rev_check.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--root", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr[:200]}")
        return result.stdout

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


async def scope_diff(project_path: Path, experiment_id: int | None = None) -> str:
    """Scope a diff against the existing repo spec using a Haiku agent call.

    If experiment_id is provided, reads .factory/experiments/{id}/changes.diff.
    Otherwise, diffs between HEAD and the commit that last touched GRAPH-SPEC.md.

    Returns the agent's markdown summary of affected scope.
    """
    from factory.agents.runner import invoke_agent
    from factory.discovery.spec import resolve_spec
    from factory.spec import read_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")

    spec_content = read_spec(project_path)
    spec_rel = str(spec_path.relative_to(project_path))

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

    scope_text = result_text.strip()

    scope_path = project_path / ".factory" / "spec_update_scope.md"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(scope_text)

    log.info("spec.scope_diff", output=str(scope_path))

    return scope_text


async def update_spec(project_path: Path) -> Path:
    """Update the repo spec based on changes since last spec commit.

    1. Scope the diff
    2. Run patcher agent to update GRAPH-SPEC.md

    Returns the path to the updated GRAPH-SPEC.md.
    """
    from factory.agents.runner import invoke_agent
    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")

    scope_text = await scope_diff(project_path)

    if not scope_text or scope_text.isspace():
        log.info("spec.update.noop", reason="no changes detected")
        return spec_path

    patch_task = (
        f"Update the repo spec at {spec_path} based on the scoped changes.\n\n"
        f"## Scope of Changes\n{scope_text}\n\n"
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
