"""W13/W10: Spec Generate and Spec Update workflow definitions."""

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


def spec_generate_workflow() -> Workflow:
    """W13: Spec Generate — extract behavioral spec, annotate, validate.

    extract -> gate_extract -> annotate -> gate_annotate ->
    validate -> gate_validate -> done
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Opus extraction — produces spec_raw.md
    nodes["extract"] = AgentNode(
        id="extract",
        role=AgentRole.RESEARCHER,
        model="opus",
        prompt_template=(
            "Extract a behavioral module map from the project. "
            "Read the spec_extractor prompt at factory/agents/prompts/spec_extractor.md. "
            "Identify module boundaries, domain entities, state machines, error types, "
            "and module relationships expressed as prose. "
            "Stay at module-level granularity. "
            "Write output to .factory/spec_raw.md in the structured Markdown format."
        ),
        writes={".factory/spec_raw.md"},
    )

    # CEO gate — check extraction quality
    nodes["gate_extract"] = GateNode(
        id="gate_extract",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the extracted spec at .factory/spec_raw.md. "
            "Check: are modules identified correctly? Are domain entities captured? "
            "Are state machines documented? Any major gaps? "
            "PROCEED if the extraction is usable. RELOOP if major gaps."
        ),
        reads={".factory/spec_raw.md"},
    )

    # Researcher annotation — produces SPEC.md at project root
    nodes["annotate"] = AgentNode(
        id="annotate",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Annotate the raw spec at .factory/spec_raw.md. "
            "Read the spec_annotator prompt at factory/agents/prompts/spec_annotator.md. "
            "Produce a behavioral spec with RFC 2119 normative language, "
            "domain model, state machines, failure model, and module behavioral contracts. "
            "Write output to SPEC.md in the project root."
        ),
        reads={".factory/spec_raw.md"},
        writes={"SPEC.md"},
    )

    # CEO gate — check annotation quality and section completeness
    nodes["gate_annotate"] = GateNode(
        id="gate_annotate",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the annotated spec at SPEC.md. "
            "Check: do module behavioral contracts match the actual code? "
            "Does the spec use RFC 2119 normative language (MUST/SHOULD/MAY)? "
            "Are there scoring tables (there should NOT be)? "
            "SECTION COMPLETENESS CHECK — verify ALL of the following sections are present "
            "and non-empty: "
            "§1 Problem Statement, "
            "§2 Goals and Non-Goals (including §2.1 Goals, §2.2 Non-Goals, §2.3 Design Philosophy), "
            "§3 Project Identity, "
            "§4 Technical Stack, "
            "§5 Architecture Overview, "
            "§6 Domain Model, "
            "§7 State Machines and Lifecycles, "
            "§8 Module Specifications, "
            "§9 Shared Contracts, "
            "§10 Configuration Specification, "
            "§11 Entry Points, "
            "§12 Failure Model and Recovery, "
            "§13 Security and Safety, "
            "§14 Test and Validation Matrix, "
            "§15 Extension Points, "
            "§16 Implementation Checklist, "
            "Appendix A: Reference Algorithms. "
            "RELOOP if ANY section is missing or empty. "
            "PROCEED only if ALL 16 sections + Appendix A are present and non-empty."
        ),
        reads={"SPEC.md"},
    )

    # Validation — run automated consistency checks
    nodes["validate"] = FnNode(
        id="validate",
        command="factory spec validate {project_path}",
        notes="Run automated consistency checks on the annotated SPEC.md. Must run after annotation is CEO-approved.",
        reads={"SPEC.md"},
        writes={".factory/spec_validation.md"},
    )

    # Final quality gate
    nodes["gate_validate"] = GateNode(
        id="gate_validate",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Final quality gate for the repo spec. "
            "Read SPEC.md. Is it complete, well-structured, "
            "and under 24K tokens? PROCEED to finish."
        ),
        reads={"SPEC.md"},
    )

    edges = [
        # Extract -> gate
        Edge(source="extract", target="gate_extract"),
        Edge(source="gate_extract", target="annotate", condition=VerdictType.PROCEED),
        Edge(source="gate_extract", target="extract", condition=VerdictType.RELOOP),
        # Annotate -> gate
        Edge(source="annotate", target="gate_annotate"),
        Edge(source="gate_annotate", target="validate", condition=VerdictType.PROCEED),
        Edge(source="gate_annotate", target="annotate", condition=VerdictType.RELOOP),
        # Validate -> gate
        Edge(source="validate", target="gate_validate"),
    ]

    return Workflow(
        name="spec-generate",
        nodes=nodes,
        edges=edges,
        start_node="extract",
        trigger=None,
    )


def spec_update_workflow() -> Workflow:
    """W10: Spec Update — scope diff, patch spec, revalidate.

    diff_scope -> patch -> gate_patch -> revalidate -> gate_revalidate -> done
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Diff scoping — map changed files to affected modules
    nodes["diff_scope"] = FnNode(
        id="diff_scope",
        command="factory spec scope {project_path}",
        notes="Map git diff to affected spec modules. Must run first to scope the patch for the spec patcher.",
        writes={".factory/spec_update_scope.md"},
    )

    # Opus patcher — incrementally update SPEC.md
    nodes["patch"] = AgentNode(
        id="patch",
        role=AgentRole.RESEARCHER,
        model="opus",
        prompt_template=(
            "Patch the repo spec based on scoped changes. "
            "Read the spec_patcher prompt at factory/agents/prompts/spec_patcher.md. "
            "Read .factory/spec_update_scope.md for the list of affected modules and new files. "
            "Read SPEC.md for the current spec. "
            "Read changed source files and update affected module behavioral contracts. "
            "Add new module entries for unmapped files. "
            "Remove modules whose paths no longer exist. "
            "Write updated spec to SPEC.md."
        ),
        reads={".factory/spec_update_scope.md"},
        writes={"SPEC.md"},
    )

    # CEO gate — check patch quality
    nodes["gate_patch"] = GateNode(
        id="gate_patch",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the patched spec at SPEC.md. "
            "Check: do updates match the diff scope? Were all affected modules touched? "
            "Were new files mapped to modules? Were deleted modules removed? "
            "PROCEED if updates are reasonable. RELOOP to patch if issues."
        ),
        reads={"SPEC.md", ".factory/spec_update_scope.md"},
    )

    # Revalidation — run automated consistency checks
    nodes["revalidate"] = FnNode(
        id="revalidate",
        command="factory spec validate {project_path}",
        notes="Re-validate the spec after patching to catch regressions. Output feeds the final CEO quality gate.",
        reads={"SPEC.md"},
        writes={".factory/spec_validation.md"},
    )

    # Final quality gate
    nodes["gate_revalidate"] = GateNode(
        id="gate_revalidate",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Final quality gate for the updated spec. "
            "Read .factory/spec_validation.md. "
            "If validation errors exist, RELOOP to patch for fixes. "
            "PROCEED if the spec passes validation."
        ),
        reads={".factory/spec_validation.md"},
    )

    edges = [
        Edge(source="diff_scope", target="patch"),
        Edge(source="patch", target="gate_patch"),
        Edge(source="gate_patch", target="revalidate", condition=VerdictType.PROCEED),
        Edge(source="gate_patch", target="patch", condition=VerdictType.RELOOP),
        Edge(source="revalidate", target="gate_revalidate"),
        Edge(source="gate_revalidate", target="patch", condition=VerdictType.RELOOP),
    ]

    return Workflow(
        name="spec-update",
        nodes=nodes,
        edges=edges,
        start_node="diff_scope",
        trigger=None,
    )
