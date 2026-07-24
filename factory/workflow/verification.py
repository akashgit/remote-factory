"""Compile artifact verification from workflow graph definitions.

Pure-function module — no runtime dependencies, no shared state, no side effects.
Generates deterministic bash verification blocks and Claude Code hook
configurations from workflow graph post_checks declarations.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from factory.workflow.primitives import AgentNode, ArtifactCheck, Workflow


def checks_to_bash(checks: list[ArtifactCheck], node_id: str) -> str:
    """Convert ArtifactCheck rules into a self-contained bash script.

    Uses only shell-local variables. Exits non-zero on any failure.
    """
    lines = [f"# Artifact verification: {node_id}", "_vfail=0"]

    for check in checks:
        path = check.path
        escaped_path = path.replace("'", "'\\''")
        lines.append(f"_f=\"$PROJECT_PATH/{escaped_path}\"")

        if check.must_exist:
            lines.append(
                f'[ ! -f "$_f" ] && echo "VERIFY FAIL: {node_id}: {path} missing" && _vfail=1'
            )
            lines.append(
                f'[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: {node_id}: {path} is empty" && _vfail=1'
            )

        if check.min_size > 0:
            lines.append(
                f'[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt {check.min_size} ] '
                f'&& echo "VERIFY FAIL: {node_id}: {path} smaller than {check.min_size} bytes" && _vfail=1'
            )

        if check.must_contain:
            escaped = "|".join(re.escape(s) for s in check.must_contain)
            labels = ", ".join(check.must_contain)
            lines.append(
                f"[ -f \"$_f\" ] && ! grep -qE '{escaped}' \"$_f\" "
                f'&& echo "VERIFY FAIL: {node_id}: {path} missing required sentinel ({labels})" && _vfail=1'
            )

    lines.append(
        f'[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node={node_id}"'
        f' >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1'
    )
    lines.append(f'echo "VERIFY OK: {node_id} artifacts validated"')
    lines.append(
        f'echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node={node_id}"'
        f' >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"'
    )

    return "\n".join(lines)


def compile_agent_verification(node: AgentNode) -> str | None:
    """Compile a verification bash block for an AgentNode.

    If node.post_checks is set, uses those. Otherwise auto-generates
    must-exist checks from node.writes. Returns None for non-blocking
    nodes or nodes with no writes.
    """
    if not node.blocking:
        return None

    if node.post_checks:
        return checks_to_bash(node.post_checks, node.id)

    if not node.writes:
        return None

    auto_checks = [
        ArtifactCheck(path=path) for path in sorted(node.writes)
    ]
    return checks_to_bash(auto_checks, node.id)


def compile_fork_verification(nodes: list[AgentNode]) -> str | None:
    """Compile a combined verification block for parallel agents.

    Emitted after the wait barrier. Returns None if no agents have writes.
    """
    all_checks: list[tuple[str, list[ArtifactCheck]]] = []
    for node in nodes:
        if not node.writes and not node.post_checks:
            continue
        checks = node.post_checks if node.post_checks else [
            ArtifactCheck(path=path) for path in sorted(node.writes)
        ]
        all_checks.append((node.id, checks))

    if not all_checks:
        return None

    sections = []
    for node_id, checks in all_checks:
        sections.append(checks_to_bash(checks, node_id))

    return "\n\n".join(sections)


# ── Hook generation ──────────────────────────────────────────────


def generate_hook_script(workflow: Workflow) -> str:
    """Generate a bash hook script for PostToolUse verification.

    The script reads the JSON payload from stdin (Claude Code passes tool_name,
    tool_input, and cwd via stdin JSON), detects `factory agent <role>` calls,
    and verifies the expected artifacts for that role.
    """
    agent_checks: list[tuple[str, str]] = []

    for node in workflow.nodes.values():
        if not isinstance(node, AgentNode):
            continue
        if not node.blocking:
            continue
        verify = compile_agent_verification(node)
        if not verify:
            continue
        role = node.role.value
        agent_checks.append((role, verify))

    if not agent_checks:
        return ""

    lines = [
        "#!/usr/bin/env bash",
        "# Auto-generated PostToolUse verification hook",
        "# Compiled from workflow: " + workflow.name,
        "",
        "# Read hook payload from stdin (Claude Code passes JSON)",
        '_HOOK_INPUT=$(cat)',
        '_COMMAND=$(echo "$_HOOK_INPUT" | jq -r \'.tool_input.command // empty\')',
        'PROJECT_PATH="${CLAUDE_PROJECT_DIR:-$PWD}"',
        "",
        '[ -z "$_COMMAND" ] && exit 0',
        "",
        "# Log every hook invocation",
        'mkdir -p "$PROJECT_PATH/.factory/hooks"',
        'echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) HOOK_FIRED command=$_COMMAND"'
        ' >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"',
        "",
    ]

    for i, (role, verify_bash) in enumerate(agent_checks):
        keyword = "elif" if i > 0 else "if"
        lines.append(f'{keyword} echo "$_COMMAND" | grep -q "factory agent {role}"; then')
        for vline in verify_bash.splitlines():
            lines.append(f"  {vline}")
        lines.append("")

    lines.append("fi")
    return "\n".join(lines)


def generate_verification_settings(
    workflow: Workflow,
    hook_script_path: Path,
) -> dict[str, Any]:
    """Generate a Claude Code settings dict with PostToolUse verification hooks."""
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(hook_script_path),
                            "timeout": 30,
                        }
                    ],
                }
            ],
        }
    }


def write_verification_hooks(
    workflow: Workflow,
    target_dir: Path,
) -> Path | None:
    """Write hook script and settings.json for a workflow into target_dir.

    Returns the settings.json path, or None if the workflow has no checks.
    """
    script_content = generate_hook_script(workflow)
    if not script_content:
        return None

    hooks_dir = target_dir / ".factory" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    script_path = hooks_dir / f"verify-{workflow.name}.sh"
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    settings = generate_verification_settings(workflow, script_path)

    settings_path = hooks_dir / f"settings-{workflow.name}.json"
    settings_path.write_text(json.dumps(settings, indent=2))

    return settings_path
