"""W5: Meta Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def meta_workflow() -> Workflow:
    """W5: Meta Mode — cross-project insights -> playbook evolution + test pruning.

    insights -> Researcher -> CEO gate -> Strategist -> User gate -> apply_playbooks ->
    Archivist(async) -> test_collect -> test_researcher -> gate -> test_builder ->
    qa_verify -> gate_qa_verify(max 3)

    The archivist is non-blocking, so it fires in the background while the
    test pruning chain proceeds immediately.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Collect cross-project insights
    nodes["insights"] = FnNode(
        id="insights",
        command="factory insights {project_path}",
        notes="Collect cross-project insights from the global registry. Must run before researcher to provide data for pattern analysis.",
        writes={".factory/strategy/insights.md"},
    )

    # Researcher reads insights + playbooks
    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Read cross-project insights at .factory/strategy/insights.md and current playbooks. "
            "Identify recurring patterns, anti-patterns, and improvement opportunities. "
            "Compare agent performance across projects. "
            "Write findings to .factory/strategy/research-local.md."
        ),
        reads={".factory/strategy/insights.md"},
        writes={".factory/strategy/research-local.md"},
    )

    # CEO gate on research quality
    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Are cross-project patterns well-supported by data? "
            "Are proposed improvements actionable? Any blind spots?"
        ),
        reads={".factory/strategy/research-local.md"},
    )

    # Strategist proposes playbook diffs
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Propose specific playbook edits based on cross-project research. "
            "For each agent role, propose DO/DON'T bullet additions or removals "
            "with supporting evidence from experiment data. "
            "Write diffs to .factory/strategy/playbook-diffs.md."
        ),
        reads={".factory/strategy/research-local.md"},
        writes={".factory/strategy/playbook-diffs.md"},
    )

    # User gate for playbook approval
    nodes["gate_user"] = GateNode(
        id="gate_user",
        evaluator_type="user",
        reads={".factory/strategy/playbook-diffs.md"},
    )

    # Apply playbooks
    nodes["apply_playbooks"] = FnNode(
        id="apply_playbooks",
        command="factory ace {project_path}",
        notes="Apply user-approved playbook diffs via the ACE engine. Runs after user gate approval.",
        reads={".factory/strategy/playbook-diffs.md"},
        writes={".factory/archive/playbooks-applied.md"},
    )

    # Archivist (async, non-blocking — fires in background while test chain proceeds)
    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive playbook evolution results.",
        reads={".factory/archive/playbooks-applied.md"},
        writes={".factory/archive/meta.md"},
        blocking=False,
    )

    # Test pruning chain
    nodes["test_collect"] = FnNode(
        id="test_collect",
        command="pytest --co -q 2>/dev/null || true",
        notes="Collect test inventory via pytest dry-run. Never fails (|| true) — output feeds the test pruning researcher.",
        writes={".factory/strategy/test-inventory.md"},
    )

    nodes["test_researcher"] = AgentNode(
        id="test_researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Analyze test inventory for redundant, dead, or flaky tests. "
            "Identify tests that overlap, test nothing meaningful, or are consistently flaky. "
            "Write findings to .factory/strategy/test-analysis.md with specific test names "
            "and reasons for removal."
        ),
        reads={".factory/strategy/test-inventory.md"},
        writes={".factory/strategy/test-analysis.md"},
    )

    nodes["gate_test_prune"] = GateNode(
        id="gate_test_prune",
        evaluator_type="user",
        reads={".factory/strategy/test-analysis.md"},
    )

    nodes["test_builder"] = AgentNode(
        id="test_builder",
        role=AgentRole.BUILDER,
        timeout=1800,
        prompt_template=(
            "Delete the approved redundant tests. Verify remaining suite still passes."
        ),
        reads={".factory/strategy/test-analysis.md"},
        writes={".factory/reviews/test-pruning-latest.md"},
    )

    nodes["qa_verify"] = AgentNode(
        id="qa_verify",
        role=AgentRole.HEALTH_CHECKER,
        timeout=1800,
        prompt_template=(
            "Verify the test suite still passes after pruning. "
            "Run health check and confirm no regressions. "
            "Write results to .factory/reviews/qa-verify-latest.md"
        ),
        reads={".factory/reviews/test-pruning-latest.md"},
        writes={".factory/reviews/qa-verify-latest.md"},
    )

    nodes["gate_qa_verify"] = GateNode(
        id="gate_qa_verify",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review QA verification of test pruning. PROCEED if tests still pass. "
            "RELOOP to test_builder (max 3 iterations) if regressions found."
        ),
        reads={".factory/reviews/qa-verify-latest.md"},
    )

    edges = [
        # Insights -> researcher
        Edge(source="insights", target="researcher"),
        # Researcher -> CEO gate
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        # Strategist -> user gate
        Edge(source="strategist", target="gate_user"),
        Edge(source="gate_user", target="apply_playbooks", condition=VerdictType.PROCEED),
        Edge(source="gate_user", target="strategist", condition=VerdictType.RELOOP),
        # Apply -> archivist (non-blocking) -> test chain
        Edge(source="apply_playbooks", target="archivist"),
        Edge(source="archivist", target="test_collect"),
        # Test pruning branch
        Edge(source="test_collect", target="test_researcher"),
        Edge(source="test_researcher", target="gate_test_prune"),
        Edge(source="gate_test_prune", target="test_builder", condition=VerdictType.PROCEED),
        Edge(source="gate_test_prune", target="test_researcher", condition=VerdictType.RELOOP),
        # QA verification after test pruning
        Edge(source="test_builder", target="qa_verify"),
        Edge(source="qa_verify", target="gate_qa_verify"),
        Edge(source="gate_qa_verify", target="test_builder", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "meta"

    return Workflow(
        name="meta",
        nodes=nodes,
        edges=edges,
        start_node="insights",
        trigger=trigger,
    )
