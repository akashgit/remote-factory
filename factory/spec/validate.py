"""Spec validation — single Haiku agent call replaces all structural checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.spec._json_util import extract_json

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
Return a JSON object:
{{
  "errors": ["<message>", ...],
  "warnings": ["<message>", ...]
}}
Errors = blocking issues (path not found, critical structural problems).
Warnings = advisory (missing sections, orphan modules, missing normative language).
Return ONLY the JSON object.
"""


@dataclass
class ValidationResult:
    """Result of spec validation."""

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def _format_validation_report(result: ValidationResult) -> str:
    """Format validation results as human-readable Markdown."""
    lines: list[str] = ["# Spec Validation Report", ""]

    lines.append("## Summary")
    lines.append("")
    status = "PASS" if result.passed else "FAIL"
    lines.append(f"**Status:** {status}")
    lines.append(f"**Errors:** {len(result.errors)}")
    lines.append(f"**Warnings:** {len(result.warnings)}")
    lines.append("")

    if result.errors:
        lines.append("## Errors")
        lines.append("")
        for err in result.errors:
            lines.append(f"- {err}")
        lines.append("")

    if result.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warn in result.warnings:
            lines.append(f"- {warn}")
        lines.append("")

    return "\n".join(lines)


async def validate_spec(project_path: Path) -> ValidationResult:
    """Validate GRAPH-SPEC.md against the actual project using a single Haiku agent call.

    Writes results to .factory/spec_validation.md.
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

    result = ValidationResult()

    if code != 0:
        result.warnings.append(f"Validation agent failed (exit {code})")
    else:
        try:
            data = extract_json(result_text)
            if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
                data = data[0]
            if isinstance(data, dict):
                for key in ("errors", "warnings"):
                    val = data.get(key, [])
                    if isinstance(val, list):
                        getattr(result, key).extend(val)
                    elif isinstance(val, str):
                        getattr(result, key).append(val)
                    else:
                        log.warning(
                            "spec.validate.unexpected_type", field=key, type=type(val).__name__
                        )
            else:
                raise ValueError(f"Expected dict, got {type(data).__name__}")
        except ValueError:
            result.warnings.append("Could not parse validation agent output")

    report = _format_validation_report(result)
    output_path = project_path / ".factory" / "spec_validation.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    log.info(
        "spec.validate.complete",
        errors=len(result.errors),
        warnings=len(result.warnings),
        output=str(output_path),
    )

    return result
