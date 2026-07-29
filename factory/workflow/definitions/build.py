"""W1: Build Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions._shared import DOC_FRESHNESS_GATE_PROMPT, _deep_qa_subgraph
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
    Workflow,
)


def build_workflow() -> Workflow:
    """W1: Build Mode — new project from idea/spec.

    Fork(3 researchers) → Join → CEO gate → Strategist → CEO gate →
    Archivist(async) → Builder → CEO gate → deep-QA → gate_qa(max 3) →
    Precheck gate → Archivist(async)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Fork: 3 parallel researchers
    nodes["fork_research"] = ForkNode(
        id="fork_research",
        targets=["researcher_similar", "researcher_techstack", "researcher_pitfalls"],
    )

    nodes["researcher_similar"] = AgentNode(
        id="researcher_similar",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Similar projects research. "
            "Search the web for similar projects, existing solutions, and prior art. "
            "Analyze their strengths, weaknesses, and market positioning. "
            "Check .factory/archive/ for prior knowledge on similar builds. "
            "Write findings to .factory/strategy/research-similar.md covering: "
            "similar projects found (with links), what they do well and what's missing, "
            "differentiation opportunities."
        ),
        writes={".factory/strategy/research-similar.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-similar.md", must_exist=True, min_size=50
            )
        ],
    )
    nodes["researcher_techstack"] = AgentNode(
        id="researcher_techstack",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Tech stack research. "
            "Identify the best technology stack for this type of project. "
            "Find architecture patterns and best practices. "
            "Evaluate framework/library options with trade-offs. "
            "Write findings to .factory/strategy/research-techstack.md covering: "
            "recommended tech stack with rationale, architecture patterns, "
            "framework comparisons."
        ),
        writes={".factory/strategy/research-techstack.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-techstack.md", must_exist=True, min_size=50
            )
        ],
    )
    nodes["researcher_pitfalls"] = AgentNode(
        id="researcher_pitfalls",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Pitfalls and scope research. "
            "Identify potential pitfalls and common mistakes for this type of project. "
            "Research MVP scope best practices. "
            "Check .factory/archive/ for lessons from past builds. "
            "Write findings to .factory/strategy/research-pitfalls.md covering: "
            "potential pitfalls to avoid, MVP scope recommendation, "
            "lessons from similar past builds."
        ),
        writes={".factory/strategy/research-pitfalls.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-pitfalls.md", must_exist=True, min_size=50
            )
        ],
    )

    # Join
    nodes["join_research"] = JoinNode(
        id="join_research",
        sources=["researcher_similar", "researcher_techstack", "researcher_pitfalls"],
        reads={
            ".factory/strategy/research-similar.md",
            ".factory/strategy/research-techstack.md",
            ".factory/strategy/research-pitfalls.md",
        },
        writes={".factory/strategy/research-combined.md"},
    )

    # CEO gate on research quality
    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Is the research relevant? Does it cover the technology landscape adequately? "
            "Check for gaps in similar projects, tech stack analysis, and pitfall coverage."
        ),
        reads={".factory/strategy/research-combined.md"},
    )

    # Strategist
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Synthesize a project specification from research. "
            "Read ALL tagged research files at .factory/strategy/research-*.md. "
            "Produce a complete phased build plan. Phase 1 must be project scaffold + eval harness. "
            "Every Phase must have substantive What/Why/Expected impact fields. "
            "Build EVERYTHING in this pass. Only defer items requiring human intervention. "
            "Write the plan to .factory/strategy/current.md."
        ),
        reads={".factory/strategy/research-combined.md"},
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=200,
                must_contain=["### Phase 1", "### Architecture"],
            )
        ],
    )

    # CEO gate on strategy quality — HARD GATE
    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "HARD GATE — Builder MUST NOT start until approved. Check: "
            "1) Depth: every hypothesis has Category/What/Why/Expected impact. "
            "2) Research grounding: architecture and rationale cite research findings. "
            "3) Buildability: a Builder could implement each phase without clarifying questions. "
            "4) Phase 1 is scaffold + eval harness. "
            "5) Deferred section only contains items requiring human intervention. "
            "Write PLAN APPROVED in verdict if all checks pass."
        ),
        reads={".factory/strategy/current.md"},
    )

    # Archivist (async, non-blocking)
    nodes["archivist_plan"] = AgentNode(
        id="archivist_plan",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive the approved research and strategy.",
        reads={".factory/strategy/current.md"},
        writes={".factory/archive/plan.md"},
        blocking=False,
    )

    # Per-phase: Builder → CEO gate → deep-QA → gate_qa(max 3) → Precheck → Archivist(async)
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the next phase from .factory/strategy/current.md. "
            "Read the CEO's plan approval at .factory/reviews/ceo-verdict-strategist.md. "
            "Read CLAUDE.md and factory.md if they exist. "
            "Implement exactly what the current phase describes. Run tests. "
            "Commit changes and open a draft PR."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md",
                must_exist=True,
                min_size=500,
                must_contain=["commit"],
            )
        ],
    )

    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read builder output. Check git log and diff. "
            "Does the work match the plan for this phase? "
            "If the Builder opened a PR, read it. "
            "REDIRECT if off-scope or missed key requirements."
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

    nodes["archivist_build"] = AgentNode(
        id="archivist_build",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive the build phase results.",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/archive/build.md"},
        blocking=False,
    )

    nodes["spec_generate"] = FnNode(
        id="spec_generate",
        command="factory spec generate {project_path}",
        notes="Generate the project specification from current state. Runs non-blocking after archival.",
        blocking=False,
    )

    # Edges
    edges = [
        # Fork to researchers
        Edge(source="fork_research", target="researcher_similar"),
        Edge(source="fork_research", target="researcher_techstack"),
        Edge(source="fork_research", target="researcher_pitfalls"),
        # Researchers to join
        Edge(source="researcher_similar", target="join_research"),
        Edge(source="researcher_techstack", target="join_research"),
        Edge(source="researcher_pitfalls", target="join_research"),
        # Join → research gate
        Edge(source="join_research", target="gate_research"),
        # Research gate → strategist (proceed) or back to researchers (reloop)
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="fork_research", condition=VerdictType.RELOOP),
        # Strategist → strategy gate
        Edge(source="strategist", target="gate_strategy"),
        # Strategy gate → archivist (proceed) or back (reloop)
        Edge(source="gate_strategy", target="archivist_plan", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # Archivist → builder
        Edge(source="archivist_plan", target="builder"),
        # Builder → build gate
        Edge(source="builder", target="gate_build"),
        # Build gate → deep-qa (proceed) or builder (reloop)
        Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Deep-QA internal edges
        *dq_edges,
        # adversarial_tester → gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        # gate_qa → doc freshness (proceed) or builder (reloop, max 3)
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness → precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck → archivist (proceed) or halt → archivist (error handling)
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.HALT),
        # Archivist → spec generate (non-blocking)
        Edge(source="archivist_build", target="spec_generate"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE}

    return Workflow(
        name="build",
        nodes=nodes,
        edges=edges,
        start_node="fork_research",
        trigger=trigger,
    )
