"""Apply edits to workflow Python files and recompile SKILL.md."""
from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from factory.skillopt.models import EditProposal

log = structlog.get_logger()


def _find_prompt_template_range(source: str, node_id: str) -> tuple[int, int] | None:
    """Find the character range of the prompt_template value for a node.

    Returns (start, end) indices of the prompt_template string content,
    or None if not found.
    """
    import re

    pattern = re.compile(
        rf'(nodes\["{re.escape(node_id)}"\]\s*=\s*AgentNode\([^)]*?'
        r'prompt_template\s*=\s*\()\s*',
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return None

    paren_start = source.index("(", match.end() - 2)

    depth = 1
    i = paren_start + 1
    while i < len(source) and depth > 0:
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
        i += 1

    return paren_start, i


def apply_edits_to_workflow(
    workflow_file: str,
    node_id: str,
    proposals: list[EditProposal],
) -> str:
    """Apply edit proposals to a workflow file's prompt_template.

    Reads the workflow .py file, locates the prompt_template for the
    target node, applies text edits (add/modify/remove), writes back,
    and runs factory workflow export-skills.

    Args:
        workflow_file: Path to the workflow .py file.
        node_id: The AgentNode id whose prompt_template to modify.
        proposals: The selected edit proposals to apply.

    Returns:
        The modified prompt_template text.
    """
    path = Path(workflow_file)
    source = path.read_text()

    rng = _find_prompt_template_range(source, node_id)
    if not rng:
        log.error("could not locate prompt_template", node_id=node_id)
        return ""

    start, end = rng
    template_section = source[start:end]

    modified = template_section
    for proposal in proposals:
        if proposal.edit_type == "add_rule":
            insert_point = modified.rfind('\\n"')
            if insert_point == -1:
                insert_point = modified.rfind('"')
            if insert_point > 0:
                new_rule = f"\\n{proposal.proposed_text}"
                modified = modified[:insert_point] + new_rule + modified[insert_point:]
                log.info("added rule", location=proposal.location)

        elif proposal.edit_type == "modify_rule":
            if proposal.original_text and proposal.original_text in modified:
                modified = modified.replace(
                    proposal.original_text, proposal.proposed_text, 1
                )
                log.info("modified rule", location=proposal.location)
            else:
                log.warning(
                    "original text not found for modify",
                    location=proposal.location,
                )

        elif proposal.edit_type == "remove_rule":
            if proposal.original_text and proposal.original_text in modified:
                modified = modified.replace(proposal.original_text, "", 1)
                log.info("removed rule", location=proposal.location)
            else:
                log.warning(
                    "original text not found for remove",
                    location=proposal.location,
                )

        elif proposal.edit_type == "reword_section":
            if proposal.original_text and proposal.original_text in modified:
                modified = modified.replace(
                    proposal.original_text, proposal.proposed_text, 1
                )
                log.info("reworded section", location=proposal.location)

    new_source = source[:start] + modified + source[end:]
    path.write_text(new_source)
    log.info("wrote updated workflow", path=workflow_file)

    _export_skills()

    return modified


def _export_skills() -> None:
    """Run factory workflow export-skills to recompile SKILL.md files."""
    try:
        result = subprocess.run(
            ["factory", "workflow", "export-skills"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            log.info("export-skills completed")
        else:
            log.warning("export-skills failed", stderr=result.stderr[:200])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("export-skills error", error=str(exc))


def revert_workflow(workflow_file: str) -> None:
    """Revert a workflow file to its git HEAD version."""
    try:
        subprocess.run(
            ["git", "checkout", "HEAD", "--", workflow_file],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        log.info("reverted workflow", path=workflow_file)
        _export_skills()
    except subprocess.CalledProcessError as exc:
        log.error("git checkout failed", error=str(exc))
