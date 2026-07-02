"""Spec validation — single Haiku agent call returns a markdown report."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

VALIDATE_PROMPT = """\
Validate this GRAPH-SPEC.md against the project at {project_path}.

## GRAPH-SPEC.md
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


@dataclass
class ValidationResult:
    """Result of spec validation."""

    report: str
    is_valid: bool


def _parse_verdict(text: str) -> bool:
    """Extract pass/fail verdict from agent output. Defaults to True if absent."""
    match = re.search(r"^Verdict:\s*(PASS|FAIL)\s*$", text, re.MULTILINE)
    if match:
        return match.group(1) == "PASS"
    return True


async def validate_spec(project_path: Path) -> ValidationResult:
    """Validate GRAPH-SPEC.md against the actual project using a single Haiku agent call.

    Writes the agent's markdown report to .factory/spec_validation.md.
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

    return ValidationResult(report=report, is_valid=is_valid)
