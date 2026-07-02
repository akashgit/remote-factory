"""Spec impact query — extract module subgraph via Haiku agent call."""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()

IMPACT_PROMPT = """\
Extract an impact analysis for the module "{module_name}" from this repo spec.

## GRAPH-SPEC.md
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
