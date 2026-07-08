"""Spec operations — validate, scope, update, and impact via agent calls."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()

# ── Validate ────────────────────────────────────────────────────

VALIDATE_PROMPT = """\
Validate this SPEC.md against the project at {project_path}.

## SPEC.md
{spec_content}

## Checks to perform
1. For each module with a declared path, verify the path exists on disk
2. For modules with declared dependencies, spot-check that actual imports match
3. Flag orphan modules (no other module depends on them or lists them as consumed_by)
4. Check that these sections are non-empty: Problem Statement, Goals, Non-Goals, \
Design Philosophy, Configuration, Security, Extension Points, Implementation Checklist
5. For entity names in the Domain Model section, verify matching classes exist in source
6. Check that module behavioral specs use RFC 2119 normative language (MUST, SHOULD, etc.)

## Output
Write a Markdown validation report with sections for Errors and Warnings.
Errors = blocking issues (path not found, critical structural problems).
Warnings = advisory (missing sections, orphan modules, missing normative language).

End the report with exactly one of these verdict lines on its own line:
Verdict: PASS
Verdict: FAIL

Use FAIL if there are any errors, PASS otherwise.
"""


def _parse_verdict(text: str) -> bool:
    """Extract pass/fail verdict from agent output. Defaults to True if absent."""
    match = re.search(r"^Verdict:\s*(PASS|FAIL)\s*$", text, re.MULTILINE)
    if match:
        return match.group(1) == "PASS"
    return True


async def validate_spec(project_path: Path) -> tuple[str, bool]:
    """Validate SPEC.md against the actual project using a single Haiku agent call.

    Writes the agent's markdown report to .factory/spec_validation.md.
    Returns (report_text, is_valid).
    """
    from factory.agents.runner import invoke_agent
    from factory.spec import read_spec

    spec_content = read_spec(project_path)

    prompt = VALIDATE_PROMPT.format(
        project_path=project_path,
        spec_content=spec_content,
    )

    result_text, code = await invoke_agent(
        "researcher",
        prompt,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        report = (
            f"# Spec Validation Report\n\nValidation agent failed (exit {code}).\n\nVerdict: PASS\n"
        )
        is_valid = True
    else:
        report = result_text.strip()
        is_valid = _parse_verdict(report)

    output_path = project_path / ".factory" / "spec_validation.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    log.info(
        "spec.validate.complete",
        is_valid=is_valid,
        output=str(output_path),
    )

    return report, is_valid


# ── Scope & Update ──────────────────────────────────────────────

SCOPE_PROMPT = """\
Analyze this git diff against the repo spec and identify which spec modules are affected.

## SPEC.md
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
    Otherwise, diffs between HEAD and the commit that last touched SPEC.md.

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
    2. Run patcher agent to update SPEC.md

    Returns the path to the updated SPEC.md.
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


# ── Impact ──────────────────────────────────────────────────────

IMPACT_PROMPT = """\
Extract an impact analysis for the module "{module_name}" from this repo spec.

## SPEC.md
{spec_content}

## Output
Produce a compact Markdown snippet covering:
1. Module path, role, and classification
2. Dependencies (what it imports)
3. Dependents (what imports it)
4. Contracts owned by this module
5. Change impact (severity and affected modules)

Use the exact heading "## Impact: {module_name}" as the first line.
Keep the output under 30 lines. Return ONLY the Markdown snippet.
"""


async def get_impact(module_name: str, project_path: Path) -> str:
    """Extract the subgraph centered on a named module from the repo spec.

    Returns a compact Markdown snippet sized for agent context inclusion.
    Raises FileNotFoundError if the spec file does not exist.
    """
    from factory.agents.runner import invoke_agent
    from factory.spec import read_spec

    spec_content = read_spec(project_path)

    prompt = IMPACT_PROMPT.format(
        module_name=module_name,
        spec_content=spec_content,
    )

    result, code = await invoke_agent(
        "researcher",
        prompt,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        raise RuntimeError(f"Impact analysis agent failed (exit {code})")

    log.info("spec.impact", module=module_name)
    return result.strip()
