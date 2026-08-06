"""All workflow definitions as Python functions returning Workflow objects.

W₁: Build Mode
W₂: Design Mode (= W₁ with user gate at strategy approval)
W₃: Improve Mode
W₄: Research Mode (= W₃ with baseline+failure_analyst, deep-QA with surface checks, plateau gate)
W₅: Meta Mode
W₆: Discover Mode
W₇: Review Mode
W₈: Refine Mode
W₉: Create Mode (meta-mode for creating new factory modes)
W₁₀: Spec Generate Mode
W₁₁: Spec Update Mode

All 5 core workflows (build, improve, research, refine, create) use the deep-QA
verification pipeline: 3 specialist agents (health_checker, code_reviewer,
adversarial_tester) with a single gate after code review to short-circuit on
critical bugs, replacing the monolithic QA agent.
"""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    SelectionNode,
    Study,
    SubgraphForkNode,
    VerdictType,
    Workflow,
)

# Re-export for test convenience
__all__ = [
    "DOC_FRESHNESS_GATE_PROMPT",
    "build_workflow",
    "design_workflow",
    "improve_workflow",

    "research_workflow",
    "meta_workflow",
    "discover_workflow",
    "review_workflow",
    "refine_workflow",
    "create_workflow",
    "skill_refine_workflow",
    "doc_generate_workflow",
    "doc_update_workflow",
    "spec_generate_workflow",
    "spec_update_workflow",
    "parallel_improve_workflow",
    "founder_workflow",
    "frontend_design_workflow",
    "frontend_design_scan_workflow",
    "evolve_workflow",
    "register_all",
]

DOC_FRESHNESS_GATE_PROMPT = (
    "Check the PR diff for documentation freshness. "
    "If public APIs, CLI commands, configuration options, "
    "or architecture were changed or added, corresponding documentation "
    "(README.md, CLAUDE.md, docstrings, --help text, or doc/ files) "
    "MUST be updated. PROCEED if docs are current or no doc-worthy changes "
    "exist. RELOOP to builder if documentation is stale — specify exactly "
    "which changes need doc updates."
)


# ── Deep-QA subgraph helper ─────────────────────────────────────


def _deep_qa_subgraph(
    *,
    code_reviewer_extra: str = "",
    adversarial_extra: str = "",
) -> tuple[dict[str, Any], list[Edge]]:
    """Return (nodes, internal_edges) for the 4-node deep-qa verification subgraph.

    Three specialist agents run sequentially with a single gate after
    code_reviewer to short-circuit on critical bugs:

        health_checker → code_reviewer → gate_review → adversarial_tester

    Agent prompts live in their role .md files; prompt_template is only set
    when a workflow passes extra context via code_reviewer_extra / adversarial_extra.
    The caller wires the entry edge (→ health_checker) and the exit edge
    (adversarial_tester →) into the surrounding workflow.
    """
    nodes: dict[str, Any] = {}

    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        reads={".factory/reviews/builder-latest.md", ".factory/strategy/current.md"},
        writes={".factory/reviews/health-check.md"},
    )

    nodes["code_reviewer"] = AgentNode(
        id="code_reviewer",
        role=AgentRole.CODE_REVIEWER,
        prompt_template=code_reviewer_extra,
        reads={".factory/reviews/builder-latest.md", ".factory/strategy/current.md"},
        writes={".factory/reviews/code-review.md"},
    )

    nodes["gate_review"] = GateNode(
        id="gate_review",
        evaluator_type="fn",
        evaluator_command=(
            "if grep -q 'CRITICAL_FOUND' "
            "{project_path}/.factory/reviews/code-review.md; "
            "then echo 'FAIL: critical issues found'; "
            "else echo 'PROCEED'; fi"
        ),
        reads={".factory/reviews/code-review.md"},
    )

    nodes["adversarial_tester"] = AgentNode(
        id="adversarial_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=1800,
        prompt_template=adversarial_extra,
        reads={".factory/reviews/builder-latest.md", ".factory/strategy/current.md"},
        writes={".factory/reviews/adversarial-qa.md"},
    )

    internal_edges = [
        Edge(source="health_checker", target="code_reviewer"),
        Edge(source="code_reviewer", target="gate_review"),
        Edge(source="gate_review", target="adversarial_tester", condition=VerdictType.PROCEED),
    ]

    return nodes, internal_edges


# ── W₁: Build Mode ──────────────────────────────────────────────


def build_workflow() -> Workflow:
    """W₁: Build Mode — new project from idea/spec.

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
        command="factory workflow run spec-generate {project_path}",
        notes="Generate the project specification via the gated spec-generate workflow. Runs non-blocking after archival.",
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


# ── W₂: Design Mode ─────────────────────────────────────────────


def design_workflow() -> Workflow:
    """W₂: Design Mode — W₁ with user gate at strategy approval.

    W₂ = W₁[gate_strategy ← GateNode(user)]
    """
    wf = build_workflow()

    wf.nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        reads={".factory/strategy/current.md"},
    )

    wf.name = "design"

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE} and ctx.get(
            "interactive", False
        )

    wf.trigger = trigger
    return wf


# ── W₃: Improve Mode ────────────────────────────────────────────


def improve_workflow() -> Workflow:
    """W₃: Improve Mode — study → research → strategy → per-hypothesis build/QA loop.

    Study → Researcher → CEO gate → Strategist → CEO gate →
    per-hypothesis: begin → Builder → CEO gate → deep-QA → gate_qa(max 3) →
    Precheck → finalize → Archivist(async)
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

    # Per-hypothesis: begin → builder → gate → deep-QA → gate_qa(max 3) → precheck → finalize → archivist
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
            "r = subprocess.run(['factory', 'workflow', 'run', 'spec-update', '{project_path}'], "
            "capture_output=True, text=True); "
            "print(r.stdout); print(r.stderr, file=sys.stderr); "
            "sys.exit(0)"
            '"'
        ),
        notes="Update SPEC.md via the gated spec-update workflow if it exists. Runs non-blocking after archival; skips silently if no spec file is present.",
        blocking=False,
    )

    edges = [
        # Study → researcher
        Edge(source="study", target="researcher"),
        # Researcher → research gate
        Edge(source="researcher", target="gate_research"),
        # Research gate
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        # Strategist → strategy gate
        Edge(source="strategist", target="gate_strategy"),
        # Strategy gate → apply spec diff → begin
        Edge(source="gate_strategy", target="apply_spec_diff", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # apply_spec_diff → begin
        Edge(source="apply_spec_diff", target="begin"),
        # begin → builder
        Edge(source="begin", target="builder"),
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
        # Precheck → finalize (proceed) or halt → archivist (error handling)
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist", condition=VerdictType.HALT),
        # Finalize → archivist → spec_update (non-blocking)
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


# ── W₄: Research Mode ───────────────────────────────────────────


def research_workflow() -> Workflow:
    """W₄: Research Mode — extends W₃ with baseline measurement, failure analyst,
    research command eval, and plateau detection.

    W₄ = W₃[study ← (baseline → failure_analyst → researcher),
             qa ← QA with surface constraint verification, + plateau_gate]
    """
    wf = improve_workflow()

    # Replace study with baseline measurement
    del wf.nodes["study"]

    wf.nodes["baseline"] = FnNode(
        id="baseline",
        command="factory eval {project_path}",
        notes="Run baseline evaluation to capture current scores before any changes. Must run before failure analysis.",
        writes={".factory/experiments/baseline.json"},
    )

    # Insert failure analyst
    wf.nodes["failure_analyst"] = AgentNode(
        id="failure_analyst",
        role=AgentRole.FAILURE_ANALYST,
        prompt_template=(
            "Analyze research run results. "
            "Read run artifacts at .factory/research/runs/. "
            "Read research target config from .factory/config.json. "
            "Classify failures by type and severity. "
            "Compute failure distribution. "
            "Suggest interventions within mutable surfaces only. "
            "Write to .factory/strategy/failure_analysis.md."
        ),
        reads={".factory/experiments/baseline.json"},
        writes={".factory/strategy/failure_analysis.md"},
    )

    # Update researcher to read failure analysis
    wf.nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Failure-targeted research. "
            "Read failure analysis at .factory/strategy/failure_analysis.md. "
            "Search the web for solutions to the dominant failure modes. "
            "Check .factory/archive/ for prior knowledge on these patterns. "
            "Write findings to .factory/strategy/research-local.md."
        ),
        reads={".factory/strategy/failure_analysis.md"},
        writes={".factory/strategy/research-local.md"},
    )

    # Update strategist to read failure analysis instead of observations
    wf.nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Generate research hypotheses targeting dominant failure modes. "
            "Each hypothesis must improve over the previous baseline score. "
            "Each hypothesis must name specific files from mutable_surfaces to modify. "
            "Hypotheses MUST NOT modify files in fixed_surfaces. "
            "Prioritize by expected impact on the target metric. "
            "Write 1-3 hypotheses to .factory/strategy/current.md."
        ),
        reads={".factory/strategy/research-local.md", ".factory/strategy/failure_analysis.md"},
        writes={".factory/strategy/current.md"},
    )

    # Override deep-qa subgraph with research-specific code reviewer extra
    dq_nodes, dq_edges = _deep_qa_subgraph(
        code_reviewer_extra=(
            "Verify mutable/fixed surface constraint compliance. "
            "Check that no files in fixed_surfaces were modified."
        ),
    )
    wf.nodes.update(dq_nodes)

    # Add plateau gate after finalize — checks if score improved over prior runs
    wf.nodes["plateau_gate"] = GateNode(
        id="plateau_gate",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "import json, pathlib, sys; "
            "tsv = pathlib.Path('{project_path}/.factory/results.tsv'); "
            "lines = [l for l in tsv.read_text().strip().splitlines()[1:] if l.strip()] if tsv.exists() else []; "
            "scores = []; "
            "[scores.append(float(p)) for l in lines for i, p in enumerate(l.split(chr(9))) if i == 2 and p]; "
            "recent = scores[-3:] if len(scores) >= 3 else scores; "
            "improved = len(recent) < 2 or recent[-1] > recent[-2]; "
            "print('RELOOP' if improved else 'PROCEED')"
            '"'
        ),
        reads={".factory/experiments/verdict.json"},
    )

    # Rebuild edges for research flow
    wf.edges = [
        # Baseline → failure analyst → researcher
        Edge(source="baseline", target="failure_analyst"),
        Edge(source="failure_analyst", target="researcher"),
        # Researcher → research gate
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        # Strategist → strategy gate → apply spec diff → begin
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="apply_spec_diff", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # apply_spec_diff → begin
        Edge(source="apply_spec_diff", target="begin"),
        # begin → builder
        Edge(source="begin", target="builder"),
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
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist", condition=VerdictType.HALT),
        # Finalize → archivist → spec_update (non-blocking) → plateau gate
        Edge(source="finalize", target="archivist"),
        Edge(source="archivist", target="spec_update"),
        Edge(source="spec_update", target="plateau_gate"),
        # Plateau gate: proceed (done) or reloop to baseline
        Edge(source="plateau_gate", target="baseline", condition=VerdictType.RELOOP),
    ]

    wf.name = "research"
    wf.start_node = "baseline"

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and bool(ctx.get("research_target"))

    wf.trigger = trigger
    return wf


# ── W₅: Meta Mode ───────────────────────────────────────────────


def meta_workflow() -> Workflow:
    """W₅: Meta Mode — cross-project insights → playbook evolution + test pruning.

    insights → Researcher → CEO gate → Strategist → User gate → apply_playbooks →
    Archivist(async) → test_collect → test_researcher → gate → test_builder →
    qa_verify → gate_qa_verify(max 3)

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
        # Insights → researcher
        Edge(source="insights", target="researcher"),
        # Researcher → CEO gate
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        # Strategist → user gate
        Edge(source="strategist", target="gate_user"),
        Edge(source="gate_user", target="apply_playbooks", condition=VerdictType.PROCEED),
        Edge(source="gate_user", target="strategist", condition=VerdictType.RELOOP),
        # Apply → archivist (non-blocking) → test chain
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


# ── W₆: Discover Mode ──────────────────────────────────────────


def discover_workflow() -> Workflow:
    """W₆: Discover Mode — auto-discover eval dimensions and generate eval harness.

    factory discover → CEO verify → re-detect state
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["discover"] = FnNode(
        id="discover",
        command="factory discover {project_path}",
        notes="Auto-discover eval dimensions and generate the eval harness (eval_profile.json + eval/score.py).",
        writes={
            ".factory/eval_profile.json",
            "eval/score.py",
        },
    )

    nodes["gate_discover"] = GateNode(
        id="gate_discover",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Verify the discovered eval profile makes sense. "
            "Read .factory/eval_profile.json and eval/score.py. "
            "Check: Are the dimensions relevant to this project? "
            "Does score.py look correct? Any missing dimensions?"
        ),
        reads={".factory/eval_profile.json", "eval/score.py"},
    )

    nodes["redetect"] = FnNode(
        id="redetect",
        command="factory detect {project_path}",
        notes="Re-detect project state after discovery to transition out of no_factory state.",
        reads={".factory/eval_profile.json"},
    )

    edges = [
        Edge(source="discover", target="gate_discover"),
        Edge(source="gate_discover", target="redetect", condition=VerdictType.PROCEED),
        Edge(source="gate_discover", target="discover", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.NO_FACTORY

    return Workflow(
        name="discover",
        nodes=nodes,
        edges=edges,
        start_node="discover",
        trigger=trigger,
    )


# ── W₇: Review Mode ───────────────────────────────────────────


def review_workflow() -> Workflow:
    """W₇: Review Mode — verify eval dimensions, create factory.md, baseline eval.

    eval_test → CEO gate (fix dims) → mark_reviewed → create_factory_md →
    factory_init → baseline_eval → commit → e2e_gate
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["eval_test"] = FnNode(
        id="eval_test",
        command="cd {project_path} && python eval/score.py",
        notes="Run the eval harness to test all discovered dimensions. Output is reviewed by the CEO gate to catch broken dimensions.",
        writes={".factory/reviews/eval-test-latest.md"},
    )

    nodes["gate_eval"] = GateNode(
        id="gate_eval",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Check eval output. Did all dimensions pass? "
            "If any dimension failed, dispatch the Builder to fix it "
            "(install missing tool, adjust command, remove broken dimension). "
            "PROCEED only when all dimensions produce valid scores."
        ),
        reads={".factory/reviews/eval-test-latest.md"},
    )

    nodes["mark_reviewed"] = FnNode(
        id="mark_reviewed",
        command=(
            'python3 -c "'
            "import json; from pathlib import Path; "
            "p = Path('{project_path}/.factory/eval_profile.json'); "
            "d = json.loads(p.read_text()); d['human_reviewed'] = True; "
            "p.write_text(json.dumps(d, indent=2))"
            '"'
        ),
        notes="Mark the eval profile as human-reviewed by setting the human_reviewed flag. Must run after the CEO approves all dimensions.",
        writes={".factory/eval_profile.json"},
    )

    nodes["create_factory_md"] = AgentNode(
        id="create_factory_md",
        role=AgentRole.CEO,
        prompt_template=(
            "Create factory.md from template. "
            "Copy the factory config template to the project root. "
            "Fill in: Goal, Scope, Guards, Eval command, Threshold, and Smoke Test. "
            "If .factory/eval_spec.json exists, populate the Eval Spec section. "
            "If .factory/strategy/current.md has a Research Configuration section, "
            "populate research sections (Research Target, Mutable/Fixed Surfaces, etc.)."
        ),
        reads={".factory/eval_profile.json"},
        writes={"factory.md"},
    )

    nodes["factory_init"] = FnNode(
        id="factory_init",
        command="factory init {project_path}",
        notes="Parse factory.md and generate .factory/config.json. Must run after factory.md is created.",
        reads={"factory.md"},
        writes={".factory/config.json"},
    )

    nodes["baseline_eval"] = FnNode(
        id="baseline_eval",
        command="factory eval {project_path}",
        notes="Run the first full eval after factory initialization to establish a baseline score.",
        reads={".factory/config.json"},
        writes={".factory/experiments/baseline.json"},
    )

    nodes["commit"] = FnNode(
        id="commit",
        command=(
            "cd {project_path} && git add factory.md eval/score.py .factory/ "
            '&& git commit -m "factory: initialize factory config and baseline eval"'
        ),
        notes="Commit the factory setup artifacts (factory.md, eval/score.py, .factory/) to git. Must run after baseline eval.",
        reads={"factory.md"},
    )

    nodes["gate_e2e"] = GateNode(
        id="gate_e2e",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "E2E verification gate. Verify the project runs end-to-end. "
            "Check the Smoke Test command in factory.md and run it. "
            "If this is a pre-existing project entering the factory for the first time, "
            "it MUST be verified before transitioning to Improve mode."
        ),
        reads={"factory.md", ".factory/config.json"},
    )

    edges = [
        Edge(source="eval_test", target="gate_eval"),
        Edge(source="gate_eval", target="mark_reviewed", condition=VerdictType.PROCEED),
        Edge(source="gate_eval", target="eval_test", condition=VerdictType.RELOOP),
        Edge(source="mark_reviewed", target="create_factory_md"),
        Edge(source="create_factory_md", target="factory_init"),
        Edge(source="factory_init", target="baseline_eval"),
        Edge(source="baseline_eval", target="commit"),
        Edge(source="commit", target="gate_e2e"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.EVALS_PENDING_REVIEW

    return Workflow(
        name="review",
        nodes=nodes,
        edges=edges,
        start_node="eval_test",
        trigger=trigger,
    )


# ── W₈: Refine Mode ───────────────────────────────────────────


def refine_workflow() -> Workflow:
    """W₈: Refine Mode — lightweight user-directed refinement pipeline.

    Refiner → CEO gate → tier gate → begin → create issue →
    Builder → deep-QA → gate_qa(max 3) → precheck → finalize → Archivist(async)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # R0: Classify
    nodes["refiner"] = AgentNode(
        id="refiner",
        role=AgentRole.REFINER,
        prompt_template=(
            "Classify and scope a refinement request. "
            "Read CLAUDE.md and factory.md. Analyze the codebase to identify "
            "which files need to change, estimate scope, and classify the request "
            "as Tier 1, 2, or 3. Produce the structured classification output "
            "with a Builder task description. "
            "Write the refinement plan to .factory/strategy/current.md."
        ),
        writes={".factory/reviews/refiner-latest.md", ".factory/strategy/current.md"},
    )

    # R0-review: CEO Review
    nodes["gate_refiner"] = GateNode(
        id="gate_refiner",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review Refiner classification. Is the tier classification reasonable? "
            "Are the identified files correct? Is the Builder task description "
            "specific enough? REDIRECT if the classification is wrong."
        ),
        reads={".factory/reviews/refiner-latest.md"},
    )

    # R1: Tier gate — Tier 3 exits
    nodes["gate_tier"] = GateNode(
        id="gate_tier",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from pathlib import Path; "
            "text = Path('{project_path}/.factory/reviews/refiner-latest.md').read_text(); "
            "print('HALT' if 'Tier 3' in text or 'tier 3' in text or 'TIER 3' in text else 'PROCEED')"
            '"'
        ),
        reads={".factory/reviews/refiner-latest.md"},
    )

    # R2: Begin experiment
    nodes["begin"] = FnNode(
        id="begin",
        command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
        notes="Open a new experiment for the refinement. The CEO must substitute $HYPOTHESIS with the refinement description.",
        writes={".factory/experiments/current_id"},
    )

    # R3: Create GitHub issue
    nodes["create_issue"] = FnNode(
        id="create_issue",
        command=(
            'gh issue create --title "Refine: refinement request" '
            '--label "refinement" --body "Factory refinement experiment."'
        ),
        notes="Create a GitHub issue to track the refinement. Must run after begin so the experiment ID is available.",
        reads={".factory/reviews/refiner-latest.md"},
    )

    # R4: Builder
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the refinement described in the Refiner's output. "
            "Read the GitHub issue. Read CLAUDE.md and factory.md. "
            "Implement exactly what the issue describes. Run tests. "
            "Commit and open a draft PR."
        ),
        reads={".factory/reviews/refiner-latest.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # R5: Deep-QA verification (replaces monolithic QA)
    dq_nodes, dq_edges = _deep_qa_subgraph(
        code_reviewer_extra=(
            "Run `factory guard --check-scope` to verify the refinement "
            "stays within declared scope."
        ),
    )
    nodes.update(dq_nodes)

    # R5-review: CEO gate on QA
    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read QA output. Did all verification sections pass? "
            "Are there issues that need Builder fixes? "
            "REDIRECT to Builder if issues found (max 3 iterations)."
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

    # R6: Precheck gate
    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    # R7: Finalize
    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            "factory finalize {project_path}"
            " --id $EXP_ID"
            " --verdict $VERDICT"
            ' --hypothesis "$HYPOTHESIS"'
        ),
        notes="Close the refinement experiment with a verdict. The CEO must substitute $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS.",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/experiments/verdict.json"},
    )

    # R12: Archivist (async)
    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive refinement experiment results and learnings.",
        reads={".factory/experiments/verdict.json"},
        writes={".factory/archive/refinement.md"},
        blocking=False,
    )

    edges = [
        # Refiner → CEO gate
        Edge(source="refiner", target="gate_refiner"),
        Edge(source="gate_refiner", target="gate_tier", condition=VerdictType.PROCEED),
        Edge(source="gate_refiner", target="refiner", condition=VerdictType.RELOOP),
        # Tier gate → begin (proceed) or halt (tier 3)
        Edge(source="gate_tier", target="begin", condition=VerdictType.PROCEED),
        # Begin → create issue → builder
        Edge(source="begin", target="create_issue"),
        Edge(source="create_issue", target="builder"),
        # Builder → deep-qa directly (no gate_build in refine)
        Edge(source="builder", target="health_checker"),
        # Deep-QA internal edges
        *dq_edges,
        # adversarial_tester → gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness → precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck → finalize (proceed) or halt → archivist (error handling)
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist", condition=VerdictType.HALT),
        Edge(source="finalize", target="archivist"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and bool(ctx.get("refine"))

    return Workflow(
        name="refine",
        nodes=nodes,
        edges=edges,
        start_node="refiner",
        trigger=trigger,
    )


# ── W₉: Create Mode ──────────────────────────────────────────────


def create_workflow() -> Workflow:
    """W₉: Create Mode — meta-mode for creating new factory modes.

    Takes a user description and produces a fully working workflow definition,
    SKILL.md, CLI wiring, and tests.

    Fork(3 researchers) → Join → CEO gate → Strategist → User gate →
    Archivist(async) → Builder → CEO gate → deep-QA → gate_qa(max 3) →
    Precheck gate → Archivist(async)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Fork: 3 parallel researchers
    nodes["fork_research"] = ForkNode(
        id="fork_research",
        targets=["researcher_existing", "researcher_intent", "researcher_practices"],
    )

    nodes["researcher_existing"] = AgentNode(
        id="researcher_existing",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Existing workflow analysis. "
            "If the CEO task includes '## Create Mode (Update Existing Mode)', read the "
            "**Target mode:** field and focus your analysis on that specific mode's workflow "
            "definition via `factory workflow show <target_mode>`. Document its current node "
            "sequences, gate logic, edge wiring, trigger function, and reads/writes. Also read "
            "its SKILL.md at skills/workflow-<target_mode>/SKILL.md for the generated playbook. "
            "Otherwise, read factory/workflow/definitions.py and analyze all existing workflow "
            "definitions (build, design, improve, research, meta, discover, review, refine). "
            "Document common patterns: node sequences, gate conventions, fork/join patterns, "
            "archivist placement, edge wiring, trigger functions, reads/writes declarations. "
            "Read factory/workflow/primitives.py for available node types and their fields. "
            "Read factory/workflow/skill_export.py for WORKFLOW_META format. "
            "Write findings to .factory/strategy/research-existing.md covering: "
            "node type usage patterns, common subgraphs (builder→gate→qa→gate loop), "
            "trigger function conventions, data flow patterns."
        ),
        writes={".factory/strategy/research-existing.md"},
    )

    nodes["researcher_intent"] = AgentNode(
        id="researcher_intent",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Mode description analysis. "
            "Read the user's mode description from the CEO task. "
            "If the CEO task includes '## Create Mode (Update Existing Mode)', parse the "
            "**Requested changes:** field and structure the requested modifications against "
            "the existing mode's current behavior. Identify which nodes, edges, prompts, or "
            "gates need to change and which must remain untouched. "
            "Otherwise, parse and structure the description into a new workflow specification: "
            "- Purpose and trigger conditions "
            "- Agent roles needed (which specialists) "
            "- Gate logic (user vs agent vs fn evaluators) "
            "- Data flow (what files are read/written) "
            "- Interactive vs headless requirements "
            "- Input format (text, file, drawing, flow) "
            "Write findings to .factory/strategy/research-intent.md covering: "
            "structured requirements, node candidates, suggested graph topology."
        ),
        writes={".factory/strategy/research-intent.md"},
    )

    nodes["researcher_practices"] = AgentNode(
        id="researcher_practices",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Workflow design best practices. "
            "Search the web for workflow and pipeline design patterns relevant "
            "to the described mode. Look for: DAG design patterns, agent orchestration "
            "patterns, quality gate strategies, error recovery approaches. "
            "Check .factory/archive/ for lessons from past mode creation or workflow changes. "
            "Write findings to .factory/strategy/research-practices.md covering: "
            "relevant design patterns, pitfalls to avoid, testing strategies."
        ),
        writes={".factory/strategy/research-practices.md"},
    )

    # Join
    nodes["join_research"] = JoinNode(
        id="join_research",
        sources=["researcher_existing", "researcher_intent", "researcher_practices"],
        reads={
            ".factory/strategy/research-existing.md",
            ".factory/strategy/research-intent.md",
            ".factory/strategy/research-practices.md",
        },
        writes={".factory/strategy/research-combined.md"},
    )

    # CEO gate on research quality
    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Are the existing workflow patterns well-documented? "
            "Is the user's intent clearly structured into workflow requirements? "
            "Are best practices relevant to this type of mode? Any gaps?"
        ),
        reads={".factory/strategy/research-combined.md"},
    )

    # Strategist synthesizes workflow specification
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Synthesize a workflow specification. "
            "Read ALL tagged research files at .factory/strategy/research-*.md. "
            "If the CEO task includes '## Create Mode (Update Existing Mode)', produce a "
            "change spec describing modifications to the existing workflow: which nodes/edges/"
            "prompts/gates to modify, what to add or remove, and a diff-oriented implementation "
            "plan. Include the 20-point verification checklist from the CEO task. Do NOT produce "
            "a complete new workflow definition — describe changes to the existing one. "
            "Otherwise, produce a complete specification for a new factory mode including: "
            "1) Python code for the workflow function (nodes dict, edges list, trigger) "
            "2) WORKFLOW_META entry (description, argument_hint) "
            "3) CLI wiring changes (build_parser mode choices, cmd_ceo routing, _build_ceo_task section) "
            "4) Test cases (graph validation, skill export, trigger function, registration) "
            "5) Node details: for each node, specify id, type, role, prompt_template, reads, writes "
            "6) Edge details: for each edge, specify source, target, condition "
            "7) Interactive vs headless behavior "
            "Follow conventions from existing workflows — use the same patterns for "
            "builder→gate→QA→gate loops, archivist placement, and research forks. "
            "When the new mode reuses parts of existing workflows, reference composition "
            "functions (compose_serial, trim_nodes, subgraph, prefix_nodes, describe_nodes) "
            "from factory/workflow/composition.py in the specification. Use "
            "describe_nodes(workflow()) programmatically to discover node IDs. "
            "Write the specification to .factory/strategy/current.md."
        ),
        reads={".factory/strategy/research-combined.md"},
        writes={".factory/strategy/current.md"},
    )

    # User gate for workflow spec approval — interactive
    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        reads={".factory/strategy/current.md"},
    )

    # Archivist (async, non-blocking)
    nodes["archivist_plan"] = AgentNode(
        id="archivist_plan",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive the approved workflow specification for the new mode.",
        reads={".factory/strategy/current.md"},
        writes={".factory/archive/create-plan.md"},
        blocking=False,
    )

    # Builder implements everything
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1800,
        prompt_template=(
            "Implement the workflow changes from the approved specification. "
            "Read the approved spec at .factory/strategy/current.md. "
            "Read CLAUDE.md for project conventions. "
            "If the CEO task includes '## Create Mode (Update Existing Mode)', follow the "
            "update checklist: modify the existing workflow function in definitions.py, verify "
            "the register_all() entry still resolves, update WORKFLOW_META if needed, verify all "
            "20 registration points from the CEO task, run factory workflow validate <name>, "
            "regenerate SKILL.md via factory workflow export-skills, update tests, run pytest "
            "and ruff check. "
            "Otherwise, follow the new-mode checklist: "
            "1) Add the workflow function to factory/workflow/definitions.py "
            "2) Register it in register_all() "
            "3) Add WORKFLOW_META entry in factory/workflow/skill_export.py "
            "4) Wire --mode in factory/cli.py (build_parser, cmd_ceo, _build_ceo_task) "
            "5) Run factory workflow validate <name> to verify the graph "
            "6) Run factory workflow export-skills to generate the SKILL.md "
            "7) Write tests in tests/ "
            "8) Run pytest and ruff check to verify "
            "Commit changes and open a draft PR. "
            "When the new mode is a variant of an existing workflow (e.g., a specialized "
            "improve loop, a subset of the build pipeline, or a chain of existing stages), "
            "use composition functions from factory.workflow.composition instead of copying: "
            "- compose_serial(w1, w2, end_node_w1=..., name=...) to chain workflows "
            "- trim_nodes(wf, node_ids) to remove nodes and auto-reconnect (linear-only) "
            "- Workflow.subgraph(node_ids, name=..., start_node=...) to extract a subset "
            "- prefix_nodes(wf, prefix) to namespace node IDs before composing "
            "- describe_nodes(wf) to inspect node IDs, types, and descriptions programmatically "
            "See factory/workflow/composition.py for the full API and "
            "factory/workflow/README.md 'Composition' section for examples."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # CEO gate on build
    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read builder output and PR diff. Does work match the approved spec? "
            "Verify: workflow function exists, registered in register_all(), "
            "WORKFLOW_META entry added, CLI wiring complete, tests written. "
            "REDIRECT if any component is missing."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # Deep-QA verification (replaces monolithic QA)
    dq_nodes, dq_edges = _deep_qa_subgraph(
        adversarial_extra=(
            "Run: factory workflow validate <name>, factory workflow show <name>, "
            "factory workflow export-skills --verify. Verify SKILL.md generated under "
            "skills/workflow-<name>/. Check CLI recognizes --mode <name>. "
            "Check workflow handles both interactive and headless paths."
        ),
    )
    nodes.update(dq_nodes)

    # CEO gate on QA (max 3 iterations)
    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review QA results for the new mode. PROCEED if all checks pass: "
            "workflow validates, SKILL.md generated, tests pass, CLI recognizes mode. "
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

    # Precheck gate
    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    # Archivist (async)
    nodes["archivist_build"] = AgentNode(
        id="archivist_build",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive the new mode build results and learnings.",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/archive/create-build.md"},
        blocking=False,
    )

    # Edges
    edges = [
        # Fork to researchers
        Edge(source="fork_research", target="researcher_existing"),
        Edge(source="fork_research", target="researcher_intent"),
        Edge(source="fork_research", target="researcher_practices"),
        # Researchers to join
        Edge(source="researcher_existing", target="join_research"),
        Edge(source="researcher_intent", target="join_research"),
        Edge(source="researcher_practices", target="join_research"),
        # Join → research gate
        Edge(source="join_research", target="gate_research"),
        # Research gate
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="fork_research", condition=VerdictType.RELOOP),
        # Strategist → user gate
        Edge(source="strategist", target="gate_strategy"),
        # User gate
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
        # gate_qa → doc freshness (proceed) or builder (reloop)
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness → precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck → archivist (proceed) or halt → archivist (error handling)
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.HALT),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "create"

    return Workflow(
        name="create",
        nodes=nodes,
        edges=edges,
        start_node="fork_research",
        trigger=trigger,
    )


# ── W₁₀: Skill Refine ────────────────────────────────────────────


def skill_refine_workflow() -> Workflow:
    """W₁₀: Verified skill generation pipeline.

    dag_sort → templatize → review_agent → guard(RELOOP → review_agent, max 2) →
    split → SKILL.md + SKILL.annotations.yaml

    On 3rd guard failure, falls back to unrefined templatize output.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["dag_sort"] = FnNode(
        id="dag_sort",
        command="factory workflow show {project_path}",
        notes="Dump the workflow DAG in topological order. Must run first to provide node ordering for templatization.",
        writes={".factory/strategy/dag-order.md"},
    )

    nodes["templatize"] = FnNode(
        id="templatize",
        command="factory workflow export-skills --templatize {project_path}",
        notes="Convert the workflow graph into a templatized SKILL.md with slot markers for the reviewer to refine.",
        reads={".factory/strategy/dag-order.md"},
        writes={".factory/strategy/templatized-skill.md"},
    )

    nodes["review_agent"] = AgentNode(
        id="review_agent",
        role=AgentRole.SKILL_REVIEWER,
        model="opus",
        prompt_template=(
            "Review and refine the templatized skill document. "
            "You may ONLY modify values inside double-brace slot markers (format: name::default). "
            "Do NOT change any text outside markers, annotations, or structure. "
            "Use the provided context bundle (agent prompts, CLI docs, edge topology) "
            "to make informed improvements to timeouts, task prompts, gate prompts, "
            "failure actions, and finalize commands."
        ),
        reads={".factory/strategy/templatized-skill.md"},
        writes={".factory/strategy/refined-skill.md"},
    )

    nodes["guard"] = GateNode(
        id="guard",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from factory.workflow.guard import check; "
            "from pathlib import Path; "
            "s = Path('{project_path}/.factory/strategy/templatized-skill.md').read_text(); "
            "r = Path('{project_path}/.factory/strategy/refined-skill.md').read_text(); "
            "result = check(s, r); "
            "print(result.verdict)"
            '"'
        ),
        reads={
            ".factory/strategy/templatized-skill.md",
            ".factory/strategy/refined-skill.md",
        },
    )

    nodes["split"] = FnNode(
        id="split",
        command="factory workflow export-skills --split {project_path}",
        notes="Split the guard-approved refined skill into clean SKILL.md and SKILL.annotations.yaml.",
        reads={".factory/strategy/refined-skill.md"},
        writes={"skills/SKILL.md", "skills/SKILL.annotations.yaml"},
    )

    edges = [
        Edge(source="dag_sort", target="templatize"),
        Edge(source="templatize", target="review_agent"),
        Edge(source="review_agent", target="guard"),
        Edge(source="guard", target="split", condition=VerdictType.PROCEED),
        Edge(source="guard", target="review_agent", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "skill-refine"

    return Workflow(
        name="skill-refine",
        nodes=nodes,
        edges=edges,
        start_node="dag_sort",
        trigger=trigger,
    )


# ── W₁₁: Doc Generate ───────────────────────────────────────────


def doc_generate_workflow() -> Workflow:
    """W₁₁: Doc Generate — scan codebase and generate documentation from scratch.

    scan_project → gate_scan → generate_docs → gate_docs →
    validate_docs → gate_validate
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


# ── W₁₂: Doc Update ────────────────────────────────────────────


def doc_update_workflow() -> Workflow:
    """W₁₂: Doc Update — update documentation based on git diff scope.

    diff_scope → patch_docs → gate_patch → revalidate → gate_revalidate
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


# ── W₁₃: Spec Generate Mode ────────────────────────────────────


def spec_generate_workflow() -> Workflow:
    """W₁₃: Spec Generate — extract behavioral spec, annotate, validate.

    extract → gate_extract → annotate → gate_annotate →
    validate → gate_validate → done
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Graphify extraction — produces graph.json (local AST, no LLM cost)
    nodes["extract"] = FnNode(
        id="extract",
        command="factory graph extract {project_path}",
        notes="Run graphify to extract a code knowledge graph from the project source.",
        writes={"graph.json"},
    )

    # CEO gate — check extraction quality
    nodes["gate_extract"] = GateNode(
        id="gate_extract",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Check that graph.json was produced. "
            "Verify it contains nodes and edges. "
            "PROCEED if the graph was extracted successfully. RELOOP if missing or empty."
        ),
        reads={"graph.json"},
    )

    # Researcher annotation — reads graph.json directly, produces SPEC.md
    nodes["annotate"] = AgentNode(
        id="annotate",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Read the code knowledge graph at graph.json. "
            "Read the spec_annotator prompt at factory/agents/prompts/spec_annotator.md. "
            "Produce a two-tier behavioral spec with RFC 2119 normative language. "
            "Use [[graph:...]] reference links for granular module details. "
            "Write output to SPEC.md in the project root."
        ),
        reads={"graph.json"},
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
        # Extract → gate
        Edge(source="extract", target="gate_extract"),
        Edge(source="gate_extract", target="annotate", condition=VerdictType.PROCEED),
        Edge(source="gate_extract", target="extract", condition=VerdictType.RELOOP),
        # Annotate → gate
        Edge(source="annotate", target="gate_annotate"),
        Edge(source="gate_annotate", target="validate", condition=VerdictType.PROCEED),
        Edge(source="gate_annotate", target="annotate", condition=VerdictType.RELOOP),
        # Validate → gate
        Edge(source="validate", target="gate_validate"),
    ]

    return Workflow(
        name="spec-generate",
        nodes=nodes,
        edges=edges,
        start_node="extract",
        trigger=None,
    )


# ── W₁₀: Spec Update Mode ─────────────────────────────────────


def spec_update_workflow() -> Workflow:
    """W₁₀: Spec Update — scope diff, patch spec, revalidate.

    diff_scope → patch → gate_patch → revalidate → gate_revalidate → done
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Incremental graph refresh — local AST, no LLM cost
    nodes["graph_update"] = FnNode(
        id="graph_update",
        command="factory graph update {project_path}",
        notes="Refresh the code knowledge graph with latest source changes before scoping the diff.",
        writes={"graph.json"},
    )

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
        Edge(source="graph_update", target="diff_scope"),
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
        start_node="graph_update",
        trigger=None,
    )


def _design_researcher_nodes() -> dict[str, AgentNode]:
    """Shared researcher nodes used by both frontend-design and frontend-design-scan."""
    return {
        "researcher_tokens": AgentNode(
            id="researcher_tokens",
            role=AgentRole.RESEARCHER,
            prompt_template=(
                "Design token research. "
                "Find the project's main CSS/theme files (index.css, globals.css, "
                "theme.ts, tailwind.config, etc.). Extract every color token, CSS "
                "custom property, and theme variable with values for all theme modes. "
                "Search all component files for hardcoded color values (hex, rgb, hsl) "
                "that bypass the token system. Count frequencies. "
                "Document the font families, spacing scale, and border-radius tiers. "
                "Write to .factory/design-system/token-audit.md."
            ),
            writes={".factory/design-system/token-audit.md"},
        ),
        "researcher_components": AgentNode(
            id="researcher_components",
            role=AgentRole.RESEARCHER,
            prompt_template=(
                "Component inventory research. "
                "Find the project's component library directory and catalog every "
                "shared component — names, props, variant systems. Identify the "
                "primitive UI library (Radix, MUI, Chakra, Headless UI, etc.) and "
                "which components wrap it. List feature-specific components. "
                "Document UI dependencies from package.json. Map composition patterns. "
                "Write to .factory/design-system/component-inventory.md."
            ),
            writes={".factory/design-system/component-inventory.md"},
        ),
        "researcher_patterns": AgentNode(
            id="researcher_patterns",
            role=AgentRole.RESEARCHER,
            prompt_template=(
                "Layout and pattern research. "
                "Read layout.tsx, router.tsx, and every page.tsx in feature modules. "
                "Document the shell structure, page templates, data-fetching patterns "
                "(e.g. TanStack Query, SWR, Apollo, RTK Query), state management "
                "(e.g. Zustand, Redux, Pinia, Context), error handling, "
                "motion/animation vocabulary, and accessibility patterns. "
                "Write to .factory/design-system/pattern-library.md."
            ),
            writes={".factory/design-system/pattern-library.md"},
        ),
        "researcher_ux": AgentNode(
            id="researcher_ux",
            role=AgentRole.RESEARCHER,
            prompt_template=(
                "UX quality research. "
                "Analyze the project's experiential layer: animation choreography "
                "(stagger timing, easing curves, entrance sequences, coordinated "
                "transitions, duration scale, exit animations, loading states), "
                "information hierarchy (heading structure, visual weight, content "
                "density, progressive disclosure, data presentation for non-technical "
                "users), and user-friendliness patterns (plain language, contextual "
                "help, onboarding/empty states, error messages, feedback patterns). "
                "Write to .factory/design-system/ux-patterns.md."
            ),
            writes={".factory/design-system/ux-patterns.md"},
        ),
    }


# ── W₁₂: Frontend Design Mode ───────────────────────────────────


def frontend_design_workflow() -> Workflow:
    """W₁₂: Frontend Design Mode — Feature-to-UI Pipeline.

    Fork(5 design researchers) → Join → CEO gate → Design Auditor →
    CEO gate → Spec Writer → User gate → Builder → Build gate →
    Render gate → CI gate → deep-QA (design variant) →
    Consistency gate(max 3) → Doc freshness → Precheck → Archivist(async)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Phase 0: Design System Existence Check ──
    # If the design system already exists on disk (from a previous discover
    # run), skip the full research pipeline and go straight to the spec
    # writer via a lightweight staleness check. If it doesn't exist, fall
    # through to the full 5-researcher pipeline.

    nodes["gate_design_system"] = GateNode(
        id="gate_design_system",
        evaluator_type="fn",
        evaluator_command=(
            "ds={project_path}/.factory/design-system && "
            "[ -f $ds/design-baseline.json ] && [ -f $ds/rules.md ] && "
            "[ -f $ds/infra-context.md ] && echo PROCEED || "
            "echo 'reloop: design system not found'"
        ),
    )

    nodes["staleness_checker"] = AgentNode(
        id="staleness_checker",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Design system staleness check. Compare design-baseline.json "
            "and rules.md against the current codebase for drift. "
            "Write verdict (STALE/DRIFT/CURRENT) to "
            ".factory/design-system/staleness-report.md."
        ),
        writes={".factory/design-system/staleness-report.md"},
    )

    # ── Phase 1: Design System Research (5 parallel researchers) ──
    # Only reached when gate_design_system RELOOPs (no design system on disk).

    nodes["fork_design_research"] = ForkNode(
        id="fork_design_research",
        targets=[
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        ],
    )

    nodes.update(_design_researcher_nodes())

    nodes["researcher_infra"] = AgentNode(
        id="researcher_infra",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Infrastructure context research. "
            "Discover the backend deployment architecture by reading Dockerfile, "
            "docker-compose.yml, k8s/ manifests, and Helm charts. Identify what "
            "environment the backend runs in (container, K8s pod, VM, serverless) "
            "and what system tools are available inside the container. "
            "Examine the backend API architecture: framework (FastAPI, Flask, etc.), "
            "router registration pattern, how new endpoints are added, existing "
            "endpoint inventory. Map resource access patterns: how the backend "
            "reaches external resources — K8s API via in-cluster config, SSH "
            "backends, database connections, external APIs. Document data sources: "
            "where data comes from (K8s node resources, subprocess calls, database "
            "queries, external APIs) and which client libraries are available. "
            "Write to .factory/design-system/infra-context.md."
        ),
        writes={".factory/design-system/infra-context.md"},
    )

    # ── Join + Research Quality Gate ──

    nodes["join_design_research"] = JoinNode(
        id="join_design_research",
        sources=[
            "researcher_tokens", "researcher_components", "researcher_patterns",
            "researcher_ux", "researcher_infra",
        ],
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        },
    )

    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Verify all five design research artifacts exist and are substantive. "
            "token-audit.md must list actual CSS custom properties. "
            "component-inventory.md must list actual .tsx files with component names. "
            "pattern-library.md must describe actual page layout patterns. "
            "ux-patterns.md must describe actual animation, hierarchy, or UX patterns. "
            "infra-context.md must describe the deployment environment and backend "
            "API architecture. "
            "RELOOP if any artifact is empty or clearly fabricated. "
            "PROCEED if all five have real data."
        ),
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        },
    )

    # ── Phase 2: Design Auditor (synthesize baseline + rules) ──

    nodes["design_auditor"] = AgentNode(
        id="design_auditor",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Design system auditor. "
            "Read .factory/design-system/token-audit.md, component-inventory.md, "
            "pattern-library.md, ux-patterns.md, and infra-context.md. "
            "Synthesize into two outputs: "
            "(1) .factory/design-system/design-baseline.json — valid JSON with "
            "token_registry, component_inventory, pattern_library, ux_patterns, "
            "and infrastructure keys. The infrastructure key must include: "
            "deployment (type, orchestrator), container_capabilities (available "
            "and unavailable tools), resource_access (how the backend reaches "
            "external resources), api_architecture (framework, router pattern, "
            "existing endpoints), and data_sources (where data comes from). "
            "Extract actual values from the research, do not fabricate. "
            "(2) .factory/design-system/rules.md — HARD RULES section "
            "(token purity, font family, component wrappers, dark mode parity, "
            "accessibility floor, infrastructure fidelity — no unavailable system "
            "tools, use established resource access patterns, follow API registration "
            "pattern) and SOFT GUIDELINES section (spacing, border-radius, "
            "motion choreography, icons, page structure, status colors, information "
            "hierarchy, user-friendliness). "
            "If previous design-baseline.json exists, merge and flag drift. "
            "Preserve any existing MANUAL OVERRIDES section in rules.md."
        ),
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        },
        writes={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
    )

    nodes["gate_audit"] = GateNode(
        id="gate_audit",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Verify design-baseline.json is valid JSON with token_registry, "
            "component_inventory, and pattern_library keys. "
            "Verify rules.md contains both HARD RULES and SOFT GUIDELINES sections. "
            "RELOOP if malformed. PROCEED if structurally valid."
        ),
        reads={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
    )

    # ── Phase 3: UI Spec Writer + User Approval ──

    nodes["spec_writer"] = AgentNode(
        id="spec_writer",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "UI spec writer. "
            "Read .factory/design-system/design-baseline.json, rules.md, and "
            "infra-context.md for design system and infrastructure constraints. "
            "The feature goal is in the CEO's task prompt (from --focus). "
            "Produce .factory/design-system/ui-spec.md with sections: Feature "
            "Description, Component Plan (reference existing components, justify "
            "any new ones), Token Usage (map each element to specific tokens), "
            "Layout, State Management, Dark Mode (both light and dark values), "
            "Accessibility, Motion, Visual Mockups, Constraints. "
            "For every data-fetching component, specify what it shows when the "
            "backend API returns 404 or is unreachable — this must be a designed "
            "empty state with guidance text, not an error message. "
            "List all API endpoints the feature depends on and whether each "
            "already exists in the backend. If an endpoint is missing, specify "
            "the backend route, data source, access method (referencing "
            "infra-context.md), and response model so the Builder can implement "
            "it using only tools available in the deployment environment. "
            "VISUAL MOCKUPS: for each designed state (loading, populated, empty, "
            "unreachable), draw an ASCII wireframe using box-drawing characters "
            "showing the card layout, labels, status indicators, and content "
            "hierarchy. The user approves the spec based on these mockups. "
            "Be precise — reference actual component names and token values."
        ),
        reads={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
            ".factory/design-system/infra-context.md",
        },
        writes={".factory/design-system/ui-spec.md"},
    )

    nodes["gate_spec"] = GateNode(
        id="gate_spec",
        evaluator_type="user",
        gate_prompt=(
            "Review the UI spec. It describes what will be built and the design "
            "constraints that will be enforced. PROCEED to approve implementation, "
            "RELOOP with feedback to revise, HALT to abandon."
        ),
        reads={".factory/design-system/ui-spec.md"},
    )

    # ── Phase 4: Constrained Builder ──

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Design-constrained builder. "
            "Read .factory/design-system/ui-spec.md (the approved spec), "
            "design-baseline.json (the design system), rules.md (the rules), "
            "and infra-context.md (infrastructure constraints). "
            "Implement exactly what the spec describes. Constraints: "
            "only approved color tokens from the baseline, only declared font families, "
            "only the project's shared component library (no direct primitive library "
            "imports in feature code), established spacing values, dark mode pairs "
            "required if the project uses dark mode, aria-labels on interactive "
            "elements, the project's established icon library only. "
            "CRITICAL: every data-fetching component must handle 3 states: "
            "(1) loading/skeleton, (2) populated, (3) unavailable (API 404 or "
            "network error). The unavailable state must show a designed message "
            "like 'Coming soon' or 'Not yet configured' — NEVER 'Unable to load' "
            "or 'Failed to fetch'. Treat missing backend APIs as expected. "
            "END-TO-END: if the frontend calls a backend API that does not exist, "
            "implement the backend endpoint too. Check the project's API routes — "
            "the feature must work end-to-end, not just render a loading spinner. "
            "INFRASTRUCTURE: when implementing backend endpoints, check "
            "infra-context.md for deployment constraints. Use only system tools "
            "available in the container. Use established resource access patterns "
            "(e.g., K8s API client, not subprocess calls to unavailable tools). "
            "Follow the existing API router registration pattern. "
            "After implementation, start the dev server and verify the feature "
            "renders without error messages. "
            "Run tests. Commit and open a draft PR."
        ),
        reads={
            ".factory/design-system/ui-spec.md",
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
            ".factory/design-system/infra-context.md",
        },
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && npx tsc --noEmit 2>&1 && npm run lint 2>&1 "
            "&& echo PROCEED || echo FAIL"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Phase 4b: Render Verification Gate ──

    nodes["gate_render"] = GateNode(
        id="gate_render",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && ( "
            "ROOT='.'; "
            "if [ -f package.json ] && node -e "
            "\"process.exit(JSON.parse(require('fs').readFileSync("
            "'package.json','utf8')).scripts?.dev?0:1)\" 2>/dev/null; then "
            "ROOT='.'; "
            "else for d in studio web app frontend client; do "
            "if [ -f \"$d/package.json\" ] && node -e "
            "\"process.exit(JSON.parse(require('fs').readFileSync("
            "'$d/package.json','utf8')).scripts?.dev?0:1)\" 2>/dev/null; then "
            "ROOT=\"$d\"; break; fi; done; fi; "
            "if [ \"$ROOT\" = '.' ] && ! node -e "
            "\"process.exit(JSON.parse(require('fs').readFileSync("
            "'package.json','utf8')).scripts?.dev?0:1)\" 2>/dev/null; then "
            "echo 'pass: no dev server script found'; exit 0; fi; "
            "cd \"$ROOT\" && npm run dev </dev/null >/dev/null 2>&1 & "
            "DEV_PID=$!; FOUND=0; "
            "for i in $(seq 1 30); do "
            "for port in 5173 3000 4200 8080; do "
            "if curl -s -o /dev/null -w '%{http_code}' "
            "http://localhost:$port 2>/dev/null | grep -qE '^(200|304)$'; then "
            "FOUND=1; break 2; fi; done; "
            "if ! kill -0 $DEV_PID 2>/dev/null; then "
            "echo 'reloop: dev server crashed on startup'; exit 0; fi; "
            "sleep 2; done; "
            "kill $DEV_PID 2>/dev/null; wait $DEV_PID 2>/dev/null; "
            "if [ \"$FOUND\" -eq 1 ]; then "
            "echo 'pass: dev server started and responded'; "
            "else echo 'reloop: dev server did not respond within 60s'; fi "
            ")"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Phase 4c: CI Verification Gate ──

    nodes["gate_ci"] = GateNode(
        id="gate_ci",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && ( "
            "PR=$(gh pr view --json number -q .number 2>/dev/null) || true; "
            "if [ -z \"$PR\" ]; then echo 'pass: no PR found'; exit 0; fi; "
            "for i in $(seq 1 20); do "
            "BUCKETS=$(gh pr checks \"$PR\" --json bucket "
            "--jq '.[].bucket' 2>/dev/null) || true; "
            "if [ -z \"$BUCKETS\" ]; then "
            "echo 'pass: no CI checks configured'; exit 0; fi; "
            "if echo \"$BUCKETS\" | grep -qE '^(fail|cancel)$'; then "
            "NAMES=$(gh pr checks \"$PR\" --json name,bucket "
            "--jq '[.[] | select(.bucket==\"fail\" or .bucket==\"cancel\") "
            "| .name] | join(\", \")' 2>/dev/null); "
            "echo \"reloop: CI failed for PR #$PR - $NAMES\"; exit 0; fi; "
            "if ! echo \"$BUCKETS\" | grep -qE '^pending$'; then "
            "echo 'pass: all CI checks passed'; exit 0; fi; "
            "sleep 30; done; "
            "echo 'reloop: CI timed out after 10 minutes' "
            ")"
        ),
    )

    # ── Phase 5: Design-Aware Deep QA ──

    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        prompt_template=(
            "Design health check. Standard checks (tsc, lint, build) plus: "
            "verify kebab-case file naming for new .tsx files, PascalCase exports, "
            "no CSS custom property overrides of existing vars. "
            "Dev server smoke test: start the dev server, verify it responds "
            "with HTTP 200 on a common port (5173, 3000, 4200, 8080). "
            "If the server crashes on startup, report as CRITICAL. "
            "If no dev server command exists, skip this check."
        ),
        reads={
            ".factory/reviews/builder-latest.md",
            ".factory/design-system/design-baseline.json",
        },
        writes={".factory/reviews/health-check.md"},
    )

    nodes["code_reviewer"] = AgentNode(
        id="code_reviewer",
        role=AgentRole.CODE_REVIEWER,
        prompt_template=(
            "Design compliance review. Read .factory/design-system/rules.md first. "
            "For each changed file check: color usage against the token registry, "
            "component imports (no direct primitive library imports in feature code), "
            "font usage against declared families, dark mode coverage, accessibility. "
            "Use literal CRITICAL_FOUND for hard rule violations. "
            "Use WARNING for soft guideline deviations."
        ),
        reads={
            ".factory/reviews/builder-latest.md",
            ".factory/design-system/rules.md",
        },
        writes={".factory/reviews/code-review.md"},
    )

    nodes["gate_review"] = GateNode(
        id="gate_review",
        evaluator_type="fn",
        evaluator_command=(
            "if grep -q 'CRITICAL_FOUND' "
            "{project_path}/.factory/reviews/code-review.md; "
            "then echo 'reloop: critical design violations found — builder must fix'; "
            "else echo 'PROCEED'; fi"
        ),
        reads={".factory/reviews/code-review.md"},
    )

    nodes["consistency_tester"] = AgentNode(
        id="consistency_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=600,
        prompt_template=(
            "Design consistency testing. Run all check scripts in "
            ".factory/design-system/checks/ then perform soft checks: "
            "spacing analysis, border-radius analysis, animation patterns, "
            "icon consistency, status variant usage. "
            "Output both .factory/reviews/adversarial_tester-latest.md "
            "and .factory/design-system/consistency-report.json with "
            "hard_failures, soft_warnings, and summary.verdict fields."
        ),
        reads={
            ".factory/reviews/builder-latest.md",
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
        writes={
            ".factory/reviews/adversarial-qa.md",
            ".factory/design-system/consistency-report.json",
        },
    )

    nodes["gate_consistency"] = GateNode(
        id="gate_consistency",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read .factory/design-system/consistency-report.json. "
            "If hard_failure_count > 0, RELOOP to builder with failure details. "
            "If only soft_warnings exist, PROCEED (warnings surface in PR). "
            "If clean, PROCEED."
        ),
        reads={
            ".factory/reviews/adversarial-qa.md",
            ".factory/design-system/consistency-report.json",
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
        prompt_template="Archive the frontend-design cycle results.",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/archive/build.md"},
        blocking=False,
    )

    # ── Edges ──

    edges = [
        # Design system existence check (entry point)
        Edge(
            source="gate_design_system",
            target="staleness_checker",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_design_system",
            target="fork_design_research",
            condition=VerdictType.RELOOP,
        ),
        # Staleness checker → spec writer (skip research)
        Edge(source="staleness_checker", target="spec_writer"),
        # Fork to researchers (only reached via RELOOP from gate_design_system)
        Edge(source="fork_design_research", target="researcher_tokens"),
        Edge(source="fork_design_research", target="researcher_components"),
        Edge(source="fork_design_research", target="researcher_patterns"),
        Edge(source="fork_design_research", target="researcher_ux"),
        Edge(source="fork_design_research", target="researcher_infra"),
        # Researchers to join
        Edge(source="researcher_tokens", target="join_design_research"),
        Edge(source="researcher_components", target="join_design_research"),
        Edge(source="researcher_patterns", target="join_design_research"),
        Edge(source="researcher_ux", target="join_design_research"),
        Edge(source="researcher_infra", target="join_design_research"),
        # Join → research gate
        Edge(source="join_design_research", target="gate_research"),
        # Research gate
        Edge(source="gate_research", target="design_auditor", condition=VerdictType.PROCEED),
        Edge(
            source="gate_research",
            target="fork_design_research",
            condition=VerdictType.RELOOP,
        ),
        # Design auditor → audit gate
        Edge(source="design_auditor", target="gate_audit"),
        Edge(source="gate_audit", target="spec_writer", condition=VerdictType.PROCEED),
        Edge(source="gate_audit", target="design_auditor", condition=VerdictType.RELOOP),
        # Spec writer → user approval gate
        Edge(source="spec_writer", target="gate_spec"),
        Edge(source="gate_spec", target="builder", condition=VerdictType.PROCEED),
        Edge(source="gate_spec", target="spec_writer", condition=VerdictType.RELOOP),
        # Builder → build gate → render gate → CI gate
        Edge(source="builder", target="gate_build"),
        Edge(source="gate_build", target="gate_render", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Render verification gate
        Edge(source="gate_render", target="gate_ci", condition=VerdictType.PROCEED),
        Edge(source="gate_render", target="builder", condition=VerdictType.RELOOP),
        # CI verification gate
        Edge(source="gate_ci", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_ci", target="builder", condition=VerdictType.RELOOP),
        # Deep-QA: health_checker → code_reviewer → gate_review → consistency_tester
        Edge(source="health_checker", target="code_reviewer"),
        Edge(source="code_reviewer", target="gate_review"),
        Edge(
            source="gate_review", target="consistency_tester", condition=VerdictType.PROCEED
        ),
        Edge(source="gate_review", target="builder", condition=VerdictType.RELOOP),
        # Consistency tester → consistency gate
        Edge(source="consistency_tester", target="gate_consistency"),
        Edge(
            source="gate_consistency",
            target="gate_doc_freshness",
            condition=VerdictType.PROCEED,
        ),
        Edge(source="gate_consistency", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness → precheck
        Edge(
            source="gate_doc_freshness",
            target="gate_precheck",
            condition=VerdictType.PROCEED,
        ),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck → archivist
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.HALT),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "frontend-design"

    return Workflow(
        name="frontend-design",
        nodes=nodes,
        edges=edges,
        start_node="gate_design_system",
        trigger=trigger,
    )


# ── W₁₃: Frontend Design Scan — Continuous Health Monitoring ────


def frontend_design_scan_workflow() -> Workflow:
    """W₁₃: Frontend Design Scan — continuous design health monitoring.

    Fork(4 design researchers) → Join → Auditor →
    Fork(6 check scripts, full codebase) → Join →
    Health report writer → Archivist(async)

    No builder, no spec writer, no user gates — scan-only.
    Designed for use with --loop for continuous hourly scanning.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Phase 1: Design System Research (4 parallel researchers) ──

    nodes["fork_scan_research"] = ForkNode(
        id="fork_scan_research",
        targets=[
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
        ],
    )

    nodes.update(_design_researcher_nodes())

    nodes["join_scan_research"] = JoinNode(
        id="join_scan_research",
        sources=["researcher_tokens", "researcher_components", "researcher_patterns", "researcher_ux"],
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
        },
    )

    # ── Phase 2: Auditor (synthesize baseline) ──

    nodes["scan_auditor"] = AgentNode(
        id="scan_auditor",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Design system auditor (scan mode). "
            "Read all four research files: token-audit.md, component-inventory.md, "
            "pattern-library.md, and ux-patterns.md. Synthesize into "
            "design-baseline.json and rules.md. "
            "If previous design-baseline.json exists, diff and report drift. "
            "This is a scan-only run — no features will be built."
        ),
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
        },
        writes={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
    )

    # ── Phase 3: Run all 6 check scripts (full codebase scan) ──

    check_scripts = [
        ("check_token_purity", "check-token-purity.sh"),
        ("check_dark_mode", "check-dark-mode.sh"),
        ("check_a11y", "check-a11y-baseline.sh"),
        ("check_component_import", "check-component-import.sh"),
        ("check_font_family", "check-font-family.sh"),
        ("check_patterns", "check-patterns.sh"),
    ]

    nodes["fork_scan_checks"] = ForkNode(
        id="fork_scan_checks",
        targets=[name for name, _ in check_scripts],
    )

    for name, script in check_scripts:
        nodes[name] = FnNode(
            id=name,
            command=(
                f"cd {{project_path}} && SCAN_MODE=full "
                f"bash .factory/design-system/checks/{script} --score"
            ),
            reads={".factory/design-system/design-baseline.json"},
        )

    nodes["join_scan_checks"] = JoinNode(
        id="join_scan_checks",
        sources=[name for name, _ in check_scripts],
    )

    # ── Phase 4: Health Report Writer ──

    nodes["health_report_writer"] = AgentNode(
        id="health_report_writer",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Design health report writer. "
            "Read the output of all 6 design check scripts and the "
            "design-baseline.json. Produce .factory/design-system/health-report.json "
            "with overall_score (0.0-1.0), per-dimension scores (token_purity, "
            "dark_mode_coverage, accessibility, component_wrapping, font_compliance, "
            "pattern_adherence), issue counts, top issues list, trend data "
            "(compare with previous report if exists), and actionable recommendations."
        ),
        reads={".factory/design-system/design-baseline.json"},
        writes={".factory/design-system/health-report.json"},
    )

    # ── Phase 5: Archivist (async) ──

    nodes["archivist_scan"] = AgentNode(
        id="archivist_scan",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive the design scan results and health report.",
        reads={".factory/design-system/health-report.json"},
        writes={".factory/archive/design-scan.md"},
        blocking=False,
    )

    # ── Edges ──

    edges = [
        # Fork to researchers
        Edge(source="fork_scan_research", target="researcher_tokens"),
        Edge(source="fork_scan_research", target="researcher_components"),
        Edge(source="fork_scan_research", target="researcher_patterns"),
        Edge(source="fork_scan_research", target="researcher_ux"),
        # Researchers to join
        Edge(source="researcher_tokens", target="join_scan_research"),
        Edge(source="researcher_components", target="join_scan_research"),
        Edge(source="researcher_patterns", target="join_scan_research"),
        Edge(source="researcher_ux", target="join_scan_research"),
        # Join → auditor
        Edge(source="join_scan_research", target="scan_auditor"),
        # Auditor → fork checks
        Edge(source="scan_auditor", target="fork_scan_checks"),
        # Fork to each check
        *[Edge(source="fork_scan_checks", target=name) for name, _ in check_scripts],
        # Each check to join
        *[Edge(source=name, target="join_scan_checks") for name, _ in check_scripts],
        # Join → health report
        Edge(source="join_scan_checks", target="health_report_writer"),
        # Health report → archivist
        Edge(source="health_report_writer", target="archivist_scan"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "frontend-design-scan"

    return Workflow(
        name="frontend-design-scan",
        nodes=nodes,
        edges=edges,
        start_node="fork_scan_research",
        trigger=trigger,
    )


# ── W₁₄: Frontend Design Discover — Design System Extraction ──


def frontend_design_discover_workflow() -> Workflow:
    """W₁₄: Frontend Design Discover — extract a reusable design system.

    Fork(5 design researchers) → Join → CEO gate → Design Auditor →
    CEO gate → Archivist(async)

    No spec writer, no builder, no QA — discover-only.
    Produces human-readable, editable design system artifacts that
    persist across feature builds. Run once, edit the output, then
    use frontend-design (build) mode for each new feature without
    re-running researchers.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Phase 1: Design System Research (5 parallel researchers) ──

    nodes["fork_discover_research"] = ForkNode(
        id="fork_discover_research",
        targets=[
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        ],
    )

    nodes.update(_design_researcher_nodes())

    nodes["researcher_infra"] = AgentNode(
        id="researcher_infra",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Infrastructure context research. "
            "Discover the backend deployment architecture by reading Dockerfile, "
            "docker-compose.yml, k8s/ manifests, and Helm charts. Identify what "
            "environment the backend runs in (container, K8s pod, VM, serverless) "
            "and what system tools are available inside the container. "
            "Examine the backend API architecture: framework (FastAPI, Flask, etc.), "
            "router registration pattern, how new endpoints are added, existing "
            "endpoint inventory. Map resource access patterns: how the backend "
            "reaches external resources — K8s API via in-cluster config, SSH "
            "backends, database connections, external APIs. Document data sources: "
            "where data comes from (K8s node resources, subprocess calls, database "
            "queries, external APIs) and which client libraries are available. "
            "Write to .factory/design-system/infra-context.md."
        ),
        writes={".factory/design-system/infra-context.md"},
    )

    nodes["join_discover_research"] = JoinNode(
        id="join_discover_research",
        sources=[
            "researcher_tokens", "researcher_components", "researcher_patterns",
            "researcher_ux", "researcher_infra",
        ],
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        },
    )

    nodes["gate_discover_research"] = GateNode(
        id="gate_discover_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Verify all five design research artifacts exist and are substantive. "
            "token-audit.md must list actual CSS custom properties. "
            "component-inventory.md must list actual .tsx files with component names. "
            "pattern-library.md must describe actual page layout patterns. "
            "ux-patterns.md must describe actual animation, hierarchy, or UX patterns. "
            "infra-context.md must describe the deployment environment and backend "
            "API architecture. "
            "RELOOP if any artifact is empty or clearly fabricated. "
            "PROCEED if all five have real data."
        ),
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        },
    )

    # ── Phase 2: Design Auditor (synthesize baseline + rules) ──

    nodes["design_auditor"] = AgentNode(
        id="design_auditor",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Design system auditor (discover mode). "
            "Read .factory/design-system/token-audit.md, component-inventory.md, "
            "pattern-library.md, ux-patterns.md, and infra-context.md. "
            "Synthesize into two outputs: "
            "(1) .factory/design-system/design-baseline.json — valid JSON with "
            "token_registry, component_inventory, pattern_library, ux_patterns, "
            "and infrastructure keys. The infrastructure key must include: "
            "deployment (type, orchestrator), container_capabilities (available "
            "and unavailable tools), resource_access (how the backend reaches "
            "external resources), api_architecture (framework, router pattern, "
            "existing endpoints), and data_sources (where data comes from). "
            "Extract actual values from the research, do not fabricate. "
            "(2) .factory/design-system/rules.md — HARD RULES section "
            "(token purity, font family, component wrappers, dark mode parity, "
            "accessibility floor, infrastructure fidelity — no unavailable system "
            "tools, use established resource access patterns, follow API registration "
            "pattern) and SOFT GUIDELINES section (spacing, border-radius, "
            "motion choreography, icons, page structure, status colors, information "
            "hierarchy, user-friendliness). "
            "If previous design-baseline.json exists, merge and flag drift. "
            "Preserve any existing MANUAL OVERRIDES section in rules.md. "
            "This is a discover-only run — the design system files will be "
            "reviewed and edited by a human designer before feature builds."
        ),
        reads={
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        },
        writes={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
    )

    nodes["gate_discover_audit"] = GateNode(
        id="gate_discover_audit",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Verify design-baseline.json is valid JSON with token_registry, "
            "component_inventory, and pattern_library keys. "
            "Verify rules.md contains both HARD RULES and SOFT GUIDELINES sections. "
            "RELOOP if malformed. PROCEED if structurally valid."
        ),
        reads={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
    )

    # ── Phase 3: Archivist (async) ──

    nodes["archivist_discover"] = AgentNode(
        id="archivist_discover",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive the design system discovery results. "
            "Note which artifacts were produced and summarize the design system "
            "for future reference. The user should review and edit the design "
            "system files before running feature builds."
        ),
        reads={
            ".factory/design-system/design-baseline.json",
            ".factory/design-system/rules.md",
        },
        writes={".factory/archive/design-discover.md"},
        blocking=False,
    )

    # ── Edges ──

    edges = [
        # Fork to researchers
        Edge(source="fork_discover_research", target="researcher_tokens"),
        Edge(source="fork_discover_research", target="researcher_components"),
        Edge(source="fork_discover_research", target="researcher_patterns"),
        Edge(source="fork_discover_research", target="researcher_ux"),
        Edge(source="fork_discover_research", target="researcher_infra"),
        # Researchers to join
        Edge(source="researcher_tokens", target="join_discover_research"),
        Edge(source="researcher_components", target="join_discover_research"),
        Edge(source="researcher_patterns", target="join_discover_research"),
        Edge(source="researcher_ux", target="join_discover_research"),
        Edge(source="researcher_infra", target="join_discover_research"),
        # Join → research gate
        Edge(source="join_discover_research", target="gate_discover_research"),
        # Research gate
        Edge(
            source="gate_discover_research",
            target="design_auditor",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_discover_research",
            target="fork_discover_research",
            condition=VerdictType.RELOOP,
        ),
        # Design auditor → audit gate
        Edge(source="design_auditor", target="gate_discover_audit"),
        Edge(
            source="gate_discover_audit",
            target="archivist_discover",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_discover_audit",
            target="design_auditor",
            condition=VerdictType.RELOOP,
        ),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "frontend-design-discover"

    return Workflow(
        name="frontend-design-discover",
        nodes=nodes,
        edges=edges,
        start_node="fork_discover_research",
        trigger=trigger,
    )



# ── W₁₅: Evolve Mode ──────────────────────────────────────────────


def evolve_workflow() -> Workflow:
    """W₁₅: Evolve Mode — iterative code evolution via external MCP evaluation.

    Baseline(FnNode) → Researcher → CEO gate →
    loop: Strategist → CEO gate → begin → Builder → CEO gate(build) →
    Health Checker(MCP eval + score comparison) → CEO gate(eval) →
    finalize → Archivist(async) → CEO gate(convergence, RELOOP→strategist)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Phase 0: Baseline ──────────────────────────────────────
    nodes["baseline"] = FnNode(
        id="baseline",
        command=(
            'python3 -c "'
            "import json; from pathlib import Path; "
            "p = Path('{project_path}/.factory/baseline'); "
            "p.mkdir(parents=True, exist_ok=True); "
            "Path('{project_path}/.factory/evolve').mkdir(parents=True, exist_ok=True); "
            "print('Baseline directory ready. "
            "CEO must call get_benchmark_info() and evaluate_solution() via MCP, "
            "then write initial.py and eval.json to .factory/baseline/.')"
            '"'
        ),
        notes=(
            "Initialize the baseline directory. The CEO must then:\n"
            "1. Call get_benchmark_info(benchmark_name) via MCP — read the benchmark name from the ## Benchmark Target section in the CEO task\n"
            "2. Write the initial program to .factory/baseline/initial.py\n"
            "3. Call evaluate_solution(initial_program) via MCP to get baseline score\n"
            "4. Write the eval result to .factory/baseline/eval.json\n"
            "5. Write the current best code to .factory/evolve/current_best.py\n"
            "6. Write the current score to .factory/evolve/current_score.json\n"
            "7. Copy the eval result to .factory/experiments/000/eval_before.json "
            "(same content as baseline/eval.json — enables CycleAnalyzer artifact discovery)\n"
            "8. Emit eval.completed event to .factory/events.jsonl with the baseline composite score"
        ),
        writes={
            ".factory/baseline/initial.py",
            ".factory/baseline/eval.json",
            ".factory/evolve/current_best.py",
            ".factory/evolve/current_score.json",
            ".factory/experiments/000/eval_before.json",
        },
    )

    # ── Phase 1: Research ──────────────────────────────────────
    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Optimization technique research for code evolution. "
            "Read the initial program at .factory/baseline/initial.py. "
            "Identify EVOLVE-BLOCK-START/END markers to understand mutable regions. "
            "Analyze the algorithm structure, data representations, and constants. "
            "Search the web for optimization techniques relevant to the problem domain "
            "(extract domain from the benchmark name in .factory/baseline/eval.json). "
            "Read .factory/baseline/eval.json to identify the benchmark problem domain "
            "and its target metric. Based on the discovered domain, search for relevant "
            "optimization techniques, heuristics, and algorithmic strategies specific "
            "to that problem type. "
            "Read .factory/archive/ for prior knowledge on similar optimization problems. "
            "Write findings to .factory/strategy/research.md covering: "
            "code structure analysis (mutable vs fixed regions), "
            "candidate optimization techniques ordered by expected impact, "
            "parameter tuning opportunities, algorithmic alternatives."
        ),
        reads={
            ".factory/baseline/initial.py",
            ".factory/baseline/eval.json",
        },
        writes={".factory/strategy/research.md"},
    )

    # CEO gate on research quality
    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Is the optimization research relevant to the problem domain? "
            "Does it identify the EVOLVE-BLOCK boundaries correctly? "
            "Are the proposed techniques ordered by expected impact? "
            "Are there at least 3 distinct approaches to try?"
        ),
        reads={".factory/strategy/research.md"},
    )

    # ── Phase 2: Evolution Loop ────────────────────────────────

    # Strategist: propose ONE code hypothesis
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Generate ONE code modification hypothesis for the evolve loop. "
            "Read research at .factory/strategy/research.md. "
            "Read the current best code at .factory/evolve/current_best.py. "
            "Read experiment history at .factory/results.tsv and .factory/experiments/. "
            "Read the current score from .factory/evolve/current_score.json. "
            "The hypothesis MUST be a specific code change within EVOLVE-BLOCK boundaries. "
            "Follow FEEC priority: Fix (bugs) > Exploit (tune parameters of proven approach) "
            "> Explore (new algorithm) > Combine (hybrid strategies). "
            "If the last 3 experiments were all reverted, note this — the CEO will "
            "trigger fresh research. "
            "Write a single hypothesis to .factory/strategy/current.md with: "
            "Category (algorithm-change|parameter-tuning|data-structure|initialization), "
            "Rationale, Modification (specific code), Expected Impact, Risk."
        ),
        reads={
            ".factory/strategy/research.md",
            ".factory/evolve/current_best.py",
            ".factory/evolve/current_score.json",
        },
        writes={".factory/strategy/current.md"},
    )

    # CEO gate: approve hypothesis before Builder starts
    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the code modification hypothesis. Check:\n"
            "1) Is it a specific code change, not vague prose?\n"
            "2) Does it target only EVOLVE-BLOCK regions?\n"
            "3) Is the FEEC category correct?\n"
            "4) Is the expected impact plausible?\n"
            "5) Check stuck detection: if the last 3 experiments in .factory/results.tsv "
            "were all REVERT, trigger RELOOP to researcher for fresh perspective "
            "instead of proceeding to builder.\n"
            "PROCEED if hypothesis is sound and not stuck. "
            "RELOOP to strategist if hypothesis is vague or wrong category. "
            "RELOOP to researcher if stuck (3 consecutive reverts)."
        ),
        reads={".factory/strategy/current.md"},
    )

    # Begin experiment
    nodes["begin"] = FnNode(
        id="begin",
        command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
        notes=(
            "Open a new experiment for the current hypothesis. "
            "The CEO must substitute $HYPOTHESIS with the hypothesis text."
        ),
        writes={".factory/experiments/current_id"},
    )

    # Pre-eval: copy current score snapshot to experiment's eval_before.json
    nodes["pre_eval"] = FnNode(
        id="pre_eval",
        command=(
            'python3 -c "'
            "import shutil; from pathlib import Path; "
            "src = Path('{project_path}/.factory/evolve/current_score.json'); "
            "exp_dir = Path('{project_path}/.factory/experiments/$EXP_ID'); "
            "exp_dir.mkdir(parents=True, exist_ok=True); "
            "shutil.copy2(str(src), str(exp_dir / 'eval_before.json')) "
            "if src.exists() else None; "
            "print('eval_before.json written to', exp_dir)"
            '"'
        ),
        notes=(
            "Copy current score snapshot to experiment's eval_before.json. "
            "The CEO must substitute $EXP_ID with the experiment ID from begin. "
            "This enables CycleAnalyzer to compute per-experiment score deltas."
        ),
        reads={".factory/evolve/current_score.json"},
        writes={".factory/experiments/$EXP_ID/eval_before.json"},
    )

    # Builder: apply the hypothesis to produce a candidate
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1200,
        prompt_template=(
            "Apply the code modification hypothesis to produce a candidate program. "
            "Read the hypothesis at .factory/strategy/current.md. "
            "Read the current best code at .factory/evolve/current_best.py. "
            "CRITICAL CONSTRAINTS:\n"
            "- ONLY modify code between EVOLVE-BLOCK-START and EVOLVE-BLOCK-END markers\n"
            "- Preserve ALL code outside evolution markers (imports, helpers, return format)\n"
            "- Maintain function signatures and return types expected by the evaluator\n"
            "- No external dependencies beyond what\'s in the initial program\n"
            "- Validate Python syntax (AST parse check)\n"
            "Write the complete modified program to .factory/experiments/$EXP_ID/candidate.py. "
            "Also copy it to .factory/evolve/candidate.py for the evaluator."
        ),
        reads={
            ".factory/strategy/current.md",
            ".factory/evolve/current_best.py",
        },
        writes={
            ".factory/reviews/builder-latest.md",
            ".factory/evolve/candidate.py",
        },
    )

    # CEO gate on build quality
    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review builder output. Check:\n"
            "1) candidate.py exists at .factory/evolve/candidate.py\n"
            "2) Only EVOLVE-BLOCK regions were modified (diff the candidate against current_best.py)\n"
            "3) Python syntax is valid\n"
            "4) No external dependencies were added\n"
            "REDIRECT to builder if constraints violated."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # Health Checker: evaluate via MCP + score comparison
    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        timeout=600,
        prompt_template=(
            "Evaluate the candidate program via MCP and compare scores. "
            "1. Read the candidate code from .factory/evolve/candidate.py\n"
            "2. Call evaluate_solution(candidate_code) via MCP tool\n"
            "3. Parse the evaluate_solution() response fields "
            "(combined_score, validity, eval_time, and any domain-specific metrics)\n"
            "4. Read current best score from .factory/evolve/current_score.json\n"
            "5. Read baseline eval_time from .factory/baseline/eval.json\n"
            "6. Apply verdict logic:\n"
            "   - If validity == false: REVERT ('Invalid solution')\n"
            "   - If combined_score <= current_score: REVERT ('Score degraded or unchanged')\n"
            "   - If eval_time > 10 * baseline_eval_time: REVERT ('Unacceptable slowdown')\n"
            "   - Otherwise: KEEP ('Score improved')\n"
            "7. Write structured eval results as JSON to "
            ".factory/experiments/$EXP_ID/eval_after.json with these exact fields:\n"
            '   {"combined_score": <float>, "validity": <bool>, '
            '"eval_time": <float>, "sum_radii": <float>, "target_ratio": <float>}\n'
            "8. Write verdict with KEEP/REVERT and rationale to "
            ".factory/reviews/health-check.md\n"
            "Include in the verdict: score_before, score_after, delta, validity, eval_time."
        ),
        reads={
            ".factory/evolve/candidate.py",
            ".factory/evolve/current_score.json",
            ".factory/baseline/eval.json",
        },
        writes={
            ".factory/reviews/health-check.md",
            ".factory/experiments/$EXP_ID/eval_after.json",
        },
    )

    # Post-eval: emit eval.completed event to events.jsonl
    nodes["post_eval"] = FnNode(
        id="post_eval",
        command=(
            'python3 -c "'
            "import json; from pathlib import Path; from datetime import datetime, timezone; "
            "score = None; "
            "ea = Path('{project_path}/.factory/experiments/$EXP_ID/eval_after.json'); "
            "if ea.exists(): "
            "    d = json.loads(ea.read_text()); "
            "    score = d.get('combined_score', d.get('total')); "
            "if score is None: "
            "    hc = Path('{project_path}/.factory/reviews/health-check.md'); "
            "    if hc.exists(): "
            "        for line in hc.read_text().splitlines(): "
            "            if 'score_after' in line.lower() or 'combined_score' in line.lower(): "
            "                for part in line.split(':'): "
            "                    part = part.strip().rstrip(',%); '); "
            "                    try: score = float(part); break; "
            "                    except ValueError: pass; "
            "            if score is not None: break; "
            "event = {"
            "    'type': 'eval.completed', "
            "    'data': {'composite': score if score is not None else 0.0, 'exp_id': '$EXP_ID'}, "
            "    'timestamp': datetime.now(timezone.utc).isoformat(), "
            "}; "
            "events_path = Path('{project_path}/.factory/events.jsonl'); "
            "with open(events_path, 'a') as f: "
            "    f.write(json.dumps(event) + chr(10)); "
            "print('eval.completed event emitted, composite=', score)"
            '"'
        ),
        notes=(
            "Emit eval.completed event to events.jsonl after Health Checker finishes. "
            "The CEO must substitute $EXP_ID. "
            "Reads the composite score from eval_after.json (primary) or health-check.md (fallback), "
            "then appends a structured event for CycleAnalyzer._extract_scores()."
        ),
        reads={
            ".factory/experiments/$EXP_ID/eval_after.json",
            ".factory/reviews/health-check.md",
        },
        writes={".factory/events.jsonl"},
    )

    # CEO gate on eval results — applies keep/revert
    nodes["gate_eval"] = GateNode(
        id="gate_eval",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the evaluation verdict at .factory/reviews/health-check.md.\n"
            "Read the Health Checker\'s KEEP/REVERT recommendation and rationale.\n"
            "If KEEP:\n"
            "  - Update .factory/evolve/current_best.py with the candidate code\n"
            "  - Update .factory/evolve/current_score.json with the new score\n"
            "  - Set $VERDICT=keep for finalize\n"
            "If REVERT:\n"
            "  - Keep current_best.py unchanged\n"
            "  - Set $VERDICT=revert for finalize\n"
            "Then PROCEED to finalize and archival."
        ),
        reads={".factory/reviews/health-check.md"},
    )

    # Finalize experiment
    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            "factory finalize {project_path}"
            " --id $EXP_ID"
            " --verdict $VERDICT"
            ' --hypothesis "$HYPOTHESIS"'
        ),
        notes=(
            "Close the experiment with a keep/revert verdict. "
            "The CEO must substitute $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS."
        ),
        reads={".factory/reviews/health-check.md"},
        writes={".factory/experiments/verdict.json"},
    )

    # Archivist: record results (async, non-blocking)
    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive evolve experiment results and learnings. "
            "Read the experiment verdict at .factory/experiments/verdict.json. "
            "Read the hypothesis at .factory/strategy/current.md. "
            "Read the eval results at .factory/reviews/health-check.md. "
            "If KEEP: document what worked (algorithm insight, parameter sweet spot). "
            "If REVERT: document why it failed (validity issue, wrong assumption, local optimum). "
            "Write learnings to .factory/archive/experiments/$EXP_ID.md."
        ),
        reads={".factory/experiments/verdict.json", ".factory/reviews/health-check.md"},
        writes={".factory/archive/experiment.md"},
        blocking=False,
    )

    # Convergence gate: CEO checks if target reached or max iterations
    nodes["gate_convergence"] = GateNode(
        id="gate_convergence",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Check convergence criteria. Read .factory/evolve/current_score.json "
            "and .factory/results.tsv.\n"
            "Exit (PROCEED) if ANY of:\n"
            "  1. Target score reached (check factory.md convergence.target_score)\n"
            "  2. Max cycles reached (check factory.md convergence.max_cycles, default 50)\n"
            "  3. Diminishing returns: 5 consecutive cycles with improvement < 0.001\n"
            "Continue (RELOOP to strategist) otherwise.\n"
            "Log the convergence status: current_score, target, cycles_completed, "
            "recent_improvement_deltas."
        ),
        reads={
            ".factory/evolve/current_score.json",
        },
    )

    # Final archivist: blocking summary when converged
    nodes["archivist_final"] = AgentNode(
        id="archivist_final",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Final evolution summary. Write a comprehensive summary of the evolution run: "
            "total experiments, keep/revert counts, score trajectory (baseline to final), "
            "best-performing hypothesis categories, key learnings. "
            "Read .factory/results.tsv for full history. "
            "Write to .factory/archive/evolve-summary.md."
        ),
        reads={".factory/evolve/current_score.json"},
        writes={".factory/archive/evolve-summary.md"},
        blocking=True,
    )

    # ── Edges ──────────────────────────────────────────────────

    edges = [
        # Baseline → researcher
        Edge(source="baseline", target="researcher"),
        # Researcher → research gate
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),

        # Strategist → strategy gate
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="begin", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),

        # Begin → pre_eval → builder (pre_eval copies current_score.json → eval_before.json)
        Edge(source="begin", target="pre_eval"),
        Edge(source="pre_eval", target="builder"),
        # Builder → build gate
        Edge(source="builder", target="gate_build"),
        Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),

        # Health checker → post_eval → eval gate (post_eval emits eval.completed event)
        Edge(source="health_checker", target="post_eval"),
        Edge(source="post_eval", target="gate_eval"),
        Edge(source="gate_eval", target="finalize", condition=VerdictType.PROCEED),

        # Finalize → archivist (async)
        Edge(source="finalize", target="archivist"),

        # Archivist → convergence gate
        Edge(source="archivist", target="gate_convergence"),

        # Convergence: RELOOP to strategist for next cycle, PROCEED to final archivist
        Edge(source="gate_convergence", target="strategist", condition=VerdictType.RELOOP),
        Edge(source="gate_convergence", target="archivist_final", condition=VerdictType.PROCEED),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "evolve"

    return Workflow(
        name="evolve",
        nodes=nodes,
        edges=edges,
        start_node="baseline",
        trigger=trigger,
    )


# ── Registry ─────────────────────────────────────────────────────


# ── W₁₂: Parallel Improve Mode ─────────────────────────────────


def parallel_improve_workflow() -> Workflow:
    """W₁₂: Parallel Improve — study → research → strategy → fork N experiments → select best.

    Reuses the improve workflow's shared prefix (study → research → strategy),
    then forks N hypotheses into isolated git worktrees, runs the per-experiment
    subgraph concurrently (begin → builder → QA → eval), joins at a barrier,
    selects the best result, and merges the winner.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Shared prefix (identical to improve) ──

    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

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

    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Generate prioritized hypotheses for PARALLEL execution. "
            "Read the backlog at .factory/strategy/backlog.md — clear as many items as possible. "
            "Read Hypothesis Budget from observations for constraints. "
            "Read CEO research review at .factory/reviews/ceo-verdict-researcher.md. "
            "Generate MULTIPLE independent hypotheses that can run concurrently. "
            "Each hypothesis must target different files/areas to avoid merge conflicts. "
            "Tag backlog items with **Backlog item:** and new items with **New:**. "
            "Write to .factory/strategy/current.md with each hypothesis under a "
            "## Hypothesis N heading."
        ),
        reads={".factory/strategy/research-local.md", ".factory/strategy/observations.md"},
        writes={".factory/strategy/current.md"},
    )

    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "HARD GATE for parallel experiments. Check: "
            "Are hypotheses independent (target different files/areas)? "
            "Would merge conflicts be unlikely? "
            "Each specific enough to implement? Scoped to one PR each? "
            "Expected eval impact realistic? Follows FEEC priority? "
            "Write PLAN APPROVED with approved hypotheses."
        ),
        reads={".factory/strategy/current.md"},
    )

    # ── Per-experiment subgraph (runs N times in parallel worktrees) ──

    nodes["exp_begin"] = FnNode(
        id="exp_begin",
        command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
        writes={".factory/experiments/current_id"},
    )

    nodes["exp_builder"] = AgentNode(
        id="exp_builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the current hypothesis from .factory/strategy/current.md. "
            "Read CLAUDE.md and factory.md. Read the CEO strategy approval. "
            "Implement exactly what the hypothesis describes. Run tests. "
            "Commit changes."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["exp_gate_build"] = GateNode(
        id="exp_gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read builder output and diff. Does work match the hypothesis? "
            "No scope creep? Tests included? REDIRECT if off-scope."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    dq_nodes, dq_edges = _deep_qa_subgraph(
        code_reviewer_extra=" This is a parallel experiment branch.",
        adversarial_extra=" This is a parallel experiment branch.",
    )
    # Namespace deep-QA nodes for the experiment subgraph
    exp_dq_nodes: dict[str, Any] = {}
    exp_dq_edges: list[Edge] = []
    dq_rename = {nid: f"exp_{nid}" for nid in dq_nodes}
    for nid, node in dq_nodes.items():
        new_id = dq_rename[nid]
        new_node = node.model_copy(update={"id": new_id})
        exp_dq_nodes[new_id] = new_node
    for edge in dq_edges:
        exp_dq_edges.append(
            Edge(
                source=dq_rename[edge.source],
                target=dq_rename[edge.target],
                condition=edge.condition,
            )
        )
    nodes.update(exp_dq_nodes)

    nodes["exp_gate_qa"] = GateNode(
        id="exp_gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review QA results for this experiment branch. "
            "PROCEED if all checks pass. "
            "RELOOP to exp_builder (max 3 iterations) if issues found."
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
    )

    nodes["exp_gate_precheck"] = GateNode(
        id="exp_gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    nodes["exp_eval"] = FnNode(
        id="exp_eval",
        command="factory eval {project_path}",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/last_eval.json"},
    )

    # ── SubgraphForkNode: fork N experiment branches ──

    nodes["fork_experiments"] = SubgraphForkNode(
        id="fork_experiments",
        subgraph_entry="exp_begin",
        subgraph_exit="exp_eval",
        parallelism=3,
        reads={".factory/strategy/current.md"},
        writes={".factory/parallel_results.json"},
    )

    # ── JoinNode: barrier after all branches ──

    nodes["join_experiments"] = JoinNode(
        id="join_experiments",
        sources=["fork_experiments"],
        reads={".factory/parallel_results.json"},
        writes={".factory/parallel_joined.json"},
    )

    # ── SelectionNode: pick the best ──

    nodes["select_best"] = SelectionNode(
        id="select_best",
        strategy="best_score",
        reads={".factory/parallel_joined.json"},
        writes={".factory/selection_result.json"},
    )

    # ── Post-selection ──

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive parallel experiment tournament results. "
            "Record which hypotheses were tested, their scores, "
            "which one won and why, and learnings from losers."
        ),
        reads={".factory/selection_result.json"},
        writes={".factory/archive/experiment.md"},
        blocking=False,
    )

    # ── Edges ──

    # Shared prefix
    edges = [
        Edge(source="study", target="researcher"),
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="fork_experiments", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
    ]

    # Per-experiment subgraph edges
    edges.extend(
        [
            Edge(source="exp_begin", target="exp_builder"),
            Edge(source="exp_builder", target="exp_gate_build"),
            Edge(
                source="exp_gate_build", target="exp_health_checker", condition=VerdictType.PROCEED
            ),
            Edge(source="exp_gate_build", target="exp_builder", condition=VerdictType.RELOOP),
            *exp_dq_edges,
            Edge(source="exp_adversarial_tester", target="exp_gate_qa"),
            Edge(source="exp_gate_qa", target="exp_gate_precheck", condition=VerdictType.PROCEED),
            Edge(source="exp_gate_qa", target="exp_builder", condition=VerdictType.RELOOP),
            Edge(source="exp_gate_precheck", target="exp_eval", condition=VerdictType.PROCEED),
            Edge(source="exp_gate_precheck", target="exp_eval", condition=VerdictType.HALT),
        ]
    )

    # Fork → Join → Select → Archive
    edges.extend(
        [
            Edge(source="fork_experiments", target="join_experiments"),
            Edge(source="join_experiments", target="select_best"),
            Edge(source="select_best", target="archivist"),
        ]
    )

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and ctx.get("mode") == "parallel-improve"

    return Workflow(
        name="parallel-improve",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
    )


# ── W₁₃: Founder Mode ──────────────────────────────────────────


def founder_workflow() -> Workflow:
    """W₁₃: Founder Mode — rapid prototyping pipeline for fast hypothesis iteration.

    Study → Strategist → Builder → gate_tests → finalize(async)

    No research, no deep-QA, no eval scoring. Terminal — does not chain to
    other modes. Uses pass/fail tests only.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Study
    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    # Strategist — pick ONE hypothesis, skip FEEC/backlog
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Pick ONE high-leverage hypothesis to prototype. "
            "Read observations at .factory/strategy/observations.md. "
            "Skip FEEC classification and backlog grooming — just pick the most "
            "promising idea and write it to .factory/strategy/current.md. "
            "Keep it scoped: one idea, one PR, fast to implement."
        ),
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/current.md"},
    )

    # Builder — prototype quickly
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Prototype the hypothesis from .factory/strategy/current.md. "
            "Read CLAUDE.md and factory.md for project context. "
            "Prioritize getting something working over code quality. "
            "Skip edge cases and comprehensive error handling. "
            "Run tests to verify it works. Commit the changes."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # Gate — pytest + ruff pass/fail
    nodes["gate_tests"] = GateNode(
        id="gate_tests",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && python -m pytest --tb=short -q 2>&1 && ruff check . 2>&1"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # Finalize — record results, bypassing precheck (no eval scores in founder mode)
    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            "factory finalize {project_path}"
            " --id $EXP_ID"
            " --verdict $VERDICT"
            ' --hypothesis "$HYPOTHESIS"'
            " --force"
        ),
        notes=(
            "Record experiment to .factory/results.tsv, bypassing precheck gates "
            "(no QA agents or eval scores in founder mode). "
            "The CEO must substitute $EXP_ID, $VERDICT (keep/revert), and $HYPOTHESIS."
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/experiments/verdict.json"},
        blocking=False,
    )

    edges = [
        Edge(source="study", target="strategist"),
        Edge(source="strategist", target="builder"),
        Edge(source="builder", target="gate_tests"),
        Edge(source="gate_tests", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_tests", target="builder", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and ctx.get("mode") == "founder"

    return Workflow(
        name="founder",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
        terminal=True,
    )


def register_all() -> dict[str, Workflow]:
    """Build and return all workflow definitions."""
    from factory.workflow.deep_qa import workflow as deep_qa_workflow
    from factory.workflow.contributed.legacybench import workflow as legacybench_workflow
    from factory.workflow.contributed.swebench import workflow as swebench_workflow
    from factory.workflow.contributed.featurebench import workflow as featurebench_workflow
    from factory.workflow.contributed.programbench import workflow as programbench_workflow
    from factory.workflow.contributed.terminalbench import workflow as terminalbench_workflow
    from factory.workflow.contributed.tomswe import workflow as tomswe_workflow
    from factory.workflow.contributed.salitrap import workflow as salitrap_workflow

    return {
        "build": build_workflow(),
        "design": design_workflow(),
        "discover": discover_workflow(),
        "review": review_workflow(),
        "improve": improve_workflow(),
        "parallel-improve": parallel_improve_workflow(),

        "deep-qa": deep_qa_workflow(),
        "legacybench": legacybench_workflow(),
        "featurebench": featurebench_workflow(),
        "programbench": programbench_workflow(),
        "swebench": swebench_workflow(),
        "terminalbench": terminalbench_workflow(),
        "tomswe": tomswe_workflow(),
        "salitrap": salitrap_workflow(),
        "research": research_workflow(),
        "meta": meta_workflow(),
        "refine": refine_workflow(),
        "create": create_workflow(),
        "skill-refine": skill_refine_workflow(),
        "doc-generate": doc_generate_workflow(),
        "doc-update": doc_update_workflow(),
        "spec-generate": spec_generate_workflow(),
        "spec-update": spec_update_workflow(),
        "founder": founder_workflow(),
        "frontend-design": frontend_design_workflow(),
        "frontend-design-discover": frontend_design_discover_workflow(),
        "frontend-design-scan": frontend_design_scan_workflow(),
        "evolve": evolve_workflow(),
    }
