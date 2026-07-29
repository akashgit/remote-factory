"""W11/W12: Doc Generate and Doc Update workflow definitions."""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def doc_generate_workflow() -> Workflow:
    """W11: Doc Generate — scan codebase and generate documentation from scratch.

    scan_project -> gate_scan -> generate_docs -> gate_docs ->
    validate_docs -> gate_validate
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["scan_project"] = AgentNode(
        id="scan_project",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Scan the codebase for documentable surfaces. "
            "Identify public APIs, CLI commands, configuration options, "
            "architecture patterns, and entry points. "
            "Write a complete inventory to .factory/doc_scan.md."
        ),
        writes={".factory/doc_scan.md"},
    )

    nodes["gate_scan"] = GateNode(
        id="gate_scan",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Check scan completeness. Are all major documentable surfaces "
            "identified? Public APIs, CLI commands, config options, architecture, "
            "and entry points should all be covered. "
            "RELOOP if significant surfaces are missing."
        ),
        reads={".factory/doc_scan.md"},
    )

    nodes["generate_docs"] = AgentNode(
        id="generate_docs",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Generate or update documentation files based on the scan inventory "
            "at .factory/doc_scan.md. Update README.md, CLAUDE.md, and docs/ files "
            "as needed. Ensure accuracy, completeness, and clear structure."
        ),
        reads={".factory/doc_scan.md"},
        writes={"README.md", "CLAUDE.md", "docs/"},
    )

    nodes["gate_docs"] = GateNode(
        id="gate_docs",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review generated documentation. Is it accurate, complete, and "
            "well-structured? Do the docs match the scan inventory? "
            "RELOOP if documentation has gaps or inaccuracies."
        ),
        reads={"README.md", "CLAUDE.md"},
    )

    nodes["validate_docs"] = FnNode(
        id="validate_docs",
        command=(
            'python3 -c "'
            "import re, sys; from pathlib import Path; "
            "errors = []; "
            "scan = Path('{project_path}/.factory/doc_scan.md'); "
            "[errors.append(f'missing: {{p}}') "
            "for p in re.findall(r'`([^`]+\\.(?:py|md|yaml|toml|json))`', scan.read_text()) "
            "if not Path('{project_path}/' + p).exists()]; "
            "print('PROCEED' if not errors else 'FAIL: ' + '; '.join(errors[:10]))"
            '"'
        ),
        notes="Validate that all file references in the doc scan actually exist on disk. Prints PROCEED or FAIL with missing paths.",
        reads={".factory/doc_scan.md"},
    )

    nodes["gate_validate"] = GateNode(
        id="gate_validate",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Final quality gate. Review validation results and overall "
            "documentation quality. PROCEED if all references are valid "
            "and docs are ready. RELOOP if issues remain."
        ),
        reads={".factory/doc_scan.md"},
    )

    edges = [
        Edge(source="scan_project", target="gate_scan"),
        Edge(source="gate_scan", target="generate_docs", condition=VerdictType.PROCEED),
        Edge(source="gate_scan", target="scan_project", condition=VerdictType.RELOOP),
        Edge(source="generate_docs", target="gate_docs"),
        Edge(source="gate_docs", target="validate_docs", condition=VerdictType.PROCEED),
        Edge(source="gate_docs", target="generate_docs", condition=VerdictType.RELOOP),
        Edge(source="validate_docs", target="gate_validate"),
        Edge(source="gate_validate", target="validate_docs", condition=VerdictType.RELOOP),
    ]

    return Workflow(
        name="doc-generate",
        nodes=nodes,
        edges=edges,
        start_node="scan_project",
        trigger=None,
    )


def doc_update_workflow() -> Workflow:
    """W12: Doc Update — update documentation based on git diff scope.

    diff_scope -> patch_docs -> gate_patch -> revalidate -> gate_revalidate
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["diff_scope"] = FnNode(
        id="diff_scope",
        command=(
            'python3 -c "'
            "import subprocess, re, sys; from pathlib import Path; "
            "changed = subprocess.check_output("
            "['git', 'diff', '--name-only', 'HEAD~1'], text=True"
            ").strip().splitlines(); "
            "doc_files = [f for f in Path('{project_path}').rglob('*.md')]; "
            "affected = []; "
            "[affected.append(str(d)) for d in doc_files "
            "for c in changed if c in d.read_text()]; "
            "scope = '# Doc Update Scope\\n\\n## Changed source files\\n' "
            "+ '\\n'.join(f'- {{f}}' for f in changed) "
            "+ '\\n\\n## Affected doc files\\n' "
            "+ '\\n'.join(f'- {{f}}' for f in set(affected)); "
            "Path('{project_path}/.factory/doc_update_scope.md').write_text(scope); "
            "print('PROCEED')"
            '"'
        ),
        notes="Map git diff to affected documentation files. Must run first to scope the update for the patcher agent.",
        writes={".factory/doc_update_scope.md"},
    )

    nodes["patch_docs"] = AgentNode(
        id="patch_docs",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Read the scoped changes at .factory/doc_update_scope.md. "
            "Update only the affected documentation sections. "
            "Targeted updates only — do not rewrite entire files."
        ),
        reads={".factory/doc_update_scope.md"},
        writes={"README.md", "CLAUDE.md", "docs/"},
    )

    nodes["gate_patch"] = GateNode(
        id="gate_patch",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Check that documentation patches match the diff scope. "
            "Were all affected doc files touched? Do the updates accurately "
            "reflect the source changes? "
            "RELOOP if patches are incomplete or inaccurate."
        ),
        reads={".factory/doc_update_scope.md"},
    )

    nodes["revalidate"] = FnNode(
        id="revalidate",
        command=(
            'python3 -c "'
            "import re, sys; from pathlib import Path; "
            "errors = []; "
            "scope = Path('{project_path}/.factory/doc_update_scope.md'); "
            "[errors.append(f'missing: {{p}}') "
            "for p in re.findall(r'`([^`]+\\.(?:py|md|yaml|toml|json))`', scope.read_text()) "
            "if not Path('{project_path}/' + p).exists()]; "
            "print('PROCEED' if not errors else 'FAIL: ' + '; '.join(errors[:10]))"
            '"'
        ),
        notes="Re-validate file references after doc patches. Prints PROCEED or FAIL with missing paths.",
        reads={".factory/doc_update_scope.md"},
    )

    nodes["gate_revalidate"] = GateNode(
        id="gate_revalidate",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Final quality gate for documentation updates. "
            "Review validation results and confirm patches are correct. "
            "PROCEED if all references are valid. "
            "RELOOP if issues remain."
        ),
        reads={".factory/doc_update_scope.md"},
    )

    edges = [
        Edge(source="diff_scope", target="patch_docs"),
        Edge(source="patch_docs", target="gate_patch"),
        Edge(source="gate_patch", target="revalidate", condition=VerdictType.PROCEED),
        Edge(source="gate_patch", target="patch_docs", condition=VerdictType.RELOOP),
        Edge(source="revalidate", target="gate_revalidate"),
        Edge(source="gate_revalidate", target="revalidate", condition=VerdictType.RELOOP),
    ]

    return Workflow(
        name="doc-update",
        nodes=nodes,
        edges=edges,
        start_node="diff_scope",
        trigger=None,
    )
