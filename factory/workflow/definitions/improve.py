"""W3: Improve Mode and W3b: QA Mode workflow definitions."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions._shared import DOC_FRESHNESS_GATE_PROMPT, _deep_qa_subgraph
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    Study,
    VerdictType,
    Workflow,
)


def improve_workflow() -> Workflow:
    """W3: Improve Mode — study -> research -> strategy -> per-hypothesis build/QA loop.

    Study -> Researcher -> CEO gate -> Strategist -> CEO gate ->
    per-hypothesis: begin -> Builder -> CEO gate -> deep-QA -> gate_qa(max 3) ->
    Precheck -> finalize -> Archivist(async)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Study
    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    # Researcher
    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Deep research for the project. "
            "Read observations at .factory/strategy/observations.md. "
            "Analyze codebase structure, eval scores, and experiment history. "
            "Search the web for best practices relevant to weak dimensions. "
            "Check .factory/archive/ for prior knowledge. "
            "Write findings to .factory/strategy/research-local.md."
        ),
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/research-local.md"},
    )

    # CEO gate on research
    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Are observations grounded in data? Did web research surface useful patterns? "
            "Any blind spots in the analysis?"
        ),
        reads={".factory/strategy/research-local.md"},
    )

    # Strategist
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Generate prioritized hypotheses. "
            "Read the backlog at .factory/strategy/backlog.md — clear as many items as possible. "
            "Read Hypothesis Budget from observations for constraints. "
            "Read CEO research review at .factory/reviews/ceo-verdict-researcher.md. "
            "Each hypothesis must be specific, scoped to one PR, tied to observations, "
            "with expected impact on eval dimensions. "
            "Tag backlog items with **Backlog item:** and new items with **New:**. "
            "Write to .factory/strategy/current.md."
        ),
        reads={".factory/strategy/research-local.md", ".factory/strategy/observations.md"},
        writes={".factory/strategy/current.md"},
    )

    # CEO gate on strategy — HARD GATE
    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "HARD GATE. Check: specific enough to implement? Scoped to one PR? "
            "Expected eval impact realistic? Follows FEEC priority? "
            "Not redundant with reverted experiment? "
            "At least one growth hypothesis? Backlog convergence? "
            "Write PLAN APPROVED with approved hypotheses in priority order."
        ),
        reads={".factory/strategy/current.md"},
    )

    # Apply SPEC Diff from strategy to SPEC.md (no-op if absent)
    nodes["apply_spec_diff"] = FnNode(
        id="apply_spec_diff",
        command="factory spec apply-diff {project_path}",
        notes="Apply the SPEC Diff section from the strategist's plan to SPEC.md. No-op if no SPEC Diff section exists.",
        reads={".factory/strategy/current.md"},
        writes={"SPEC.md"},
    )

    # Per-hypothesis: begin -> builder -> gate -> deep-QA -> gate_qa(max 3) -> precheck -> finalize -> archivist
    nodes["begin"] = FnNode(
        id="begin",
        command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
        notes="Open a new experiment for the current hypothesis. The CEO must substitute $HYPOTHESIS with the hypothesis text.",
        writes={".factory/experiments/current_id"},
    )

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the current hypothesis from .factory/strategy/current.md. "
            "Read CLAUDE.md and factory.md. Read the CEO strategy approval. "
            "Implement exactly what the hypothesis describes. Run tests. "
            "Commit and open a draft PR."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read builder output and PR diff. Does work match the hypothesis? "
            "No scope creep? Tests included? REDIRECT if off-scope."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # Deep-QA subgraph replaces monolithic QA
    dq_nodes, dq_edges = _deep_qa_subgraph()
    nodes.update(dq_nodes)

    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review QA results. PROCEED if all checks pass. "
            "RELOOP to builder (max 3 iterations) if issues found."
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
    )

    nodes["gate_doc_freshness"] = GateNode(
        id="gate_doc_freshness",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=DOC_FRESHNESS_GATE_PROMPT,
        reads={".factory/reviews/adversarial-qa.md"},
    )

    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            "factory finalize {project_path}"
            " --id $EXP_ID"
            " --verdict $VERDICT"
            ' --hypothesis "$HYPOTHESIS"'
        ),
        notes="Close the experiment with a keep/revert verdict. The CEO must substitute $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS.",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/experiments/verdict.json"},
    )

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive experiment results and learnings.",
        reads={".factory/experiments/verdict.json"},
        writes={".factory/archive/experiment.md"},
        blocking=False,
    )

    # Non-blocking spec update — runs if SPEC.md exists at project root
    nodes["spec_update"] = FnNode(
        id="spec_update",
        command=(
            'python3 -c "'
            "from pathlib import Path; "
            "import subprocess, sys; "
            "sys.exit(0) if not Path('{project_path}/SPEC.md').is_file() else None; "
            "r = subprocess.run(['factory', 'spec', 'update', '{project_path}'], "
            "capture_output=True, text=True); "
            "print(r.stdout); print(r.stderr, file=sys.stderr); "
            "sys.exit(0)"
            '"'
        ),
        notes="Update SPEC.md if it exists. Runs non-blocking after archival; skips silently if no spec file is present.",
        blocking=False,
    )

    edges = [
        # Study -> researcher
        Edge(source="study", target="researcher"),
        # Researcher -> research gate
        Edge(source="researcher", target="gate_research"),
        # Research gate
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        # Strategist -> strategy gate
        Edge(source="strategist", target="gate_strategy"),
        # Strategy gate -> apply spec diff -> begin
        Edge(source="gate_strategy", target="apply_spec_diff", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # apply_spec_diff -> begin
        Edge(source="apply_spec_diff", target="begin"),
        # begin -> builder
        Edge(source="begin", target="builder"),
        # Builder -> build gate
        Edge(source="builder", target="gate_build"),
        # Build gate -> deep-qa (proceed) or builder (reloop)
        Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Deep-QA internal edges
        *dq_edges,
        # adversarial_tester -> gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        # gate_qa -> doc freshness (proceed) or builder (reloop, max 3)
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness -> precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck -> finalize (proceed) or halt -> archivist (error handling)
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist", condition=VerdictType.HALT),
        # Finalize -> archivist -> spec_update (non-blocking)
        Edge(source="finalize", target="archivist"),
        Edge(source="archivist", target="spec_update"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY

    return Workflow(
        name="improve",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
    )


def qa_workflow() -> Workflow:
    """W3b: QA Mode — standalone PR verification via the deep-QA pipeline.

    Extracts the deep-QA subgraph + gate_qa + gate_precheck from W3,
    removes builder RELOOP (no fix loop in QA mode), and adds post_review.

    health_checker -> code_reviewer -> gate_review -> adversarial_tester ->
    gate_qa -> gate_precheck -> post_review
    """
    wf = improve_workflow()
    deep_qa_nodes = {
        "health_checker",
        "code_reviewer",
        "gate_review",
        "adversarial_tester",
        "gate_qa",
        "gate_precheck",
    }
    sub = wf.subgraph(
        deep_qa_nodes,
        name="qa",
        start_node="health_checker",
    )

    # Clear predecessor reads — in QA mode there's no prior builder output.
    for nid in ("health_checker", "code_reviewer", "adversarial_tester"):
        node = sub.nodes[nid]
        assert isinstance(node, AgentNode)
        sub.nodes[nid] = node.model_copy(update={"reads": set()})

    # Replace gate_qa RELOOP with HALT — no builder fix loop in QA mode.
    gate_qa = sub.nodes["gate_qa"]
    assert isinstance(gate_qa, GateNode)
    sub.nodes["gate_qa"] = gate_qa.model_copy(
        update={
            "gate_prompt": gate_qa.gate_prompt.replace(
                "RELOOP to builder (max 3 iterations) if issues found.",
                "HALT if issues found — no fix loop in QA mode.",
            ),
        }
    )

    sub.nodes["post_review"] = FnNode(
        id="post_review",
        command=(
            "factory review --verdict $VERDICT --pr $PR_NUMBER"
            " --reason $REASON"
            " --qa-body-file .factory/reviews/adversarial-qa.md"
        ),
        notes="Post the QA verdict as a GitHub PR review. The CEO must substitute $VERDICT (KEEP/REVERT), $PR_NUMBER, and $REASON.",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    sub.edges = [
        # Deep-QA internal edges
        Edge(source="health_checker", target="code_reviewer"),
        Edge(source="code_reviewer", target="gate_review"),
        Edge(source="gate_review", target="adversarial_tester", condition=VerdictType.PROCEED),
        # adversarial_tester -> gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        Edge(source="gate_qa", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="post_review", condition=VerdictType.HALT),
        Edge(source="gate_precheck", target="post_review", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="post_review", condition=VerdictType.HALT),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "qa"

    sub.trigger = trigger
    return sub
