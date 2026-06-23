"""All 5 workflow definitions as Python functions returning Workflow objects.

W₁: Build Mode
W₂: Design Mode (= W₁ with user gate at strategy approval)
W₃: Improve Mode
W₄: Research Mode (= W₃ with baseline+failure_analyst, research_command eval, plateau gate)
W₅: Meta Mode
"""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
)


# ── W₁: Build Mode ──────────────────────────────────────────────


def build_workflow() -> Workflow:
    """W₁: Build Mode — new project from idea/spec.

    Fork(3 researchers) → Join → CEO gate → Strategist → CEO gate →
    Archivist(async) → Builder → CEO gate(max 3) → Evaluator → Precheck gate →
    Archivist(async)
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

    # Per-phase: Builder → CEO gate → Evaluator → Precheck → Archivist(async)
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

    nodes["evaluator"] = AgentNode(
        id="evaluator",
        role=AgentRole.EVALUATOR,
        prompt_template=(
            "Run eval: factory eval $PROJECT_PATH. "
            "Capture composite score and per-dimension breakdown. "
            "Report delta from baseline. Interpret which dimensions improved/regressed."
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/evaluator-latest.md"},
    )

    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/evaluator-latest.md"},
    )

    nodes["archivist_build"] = AgentNode(
        id="archivist_build",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive the build phase results.",
        reads={".factory/reviews/evaluator-latest.md"},
        writes={".factory/archive/build.md"},
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
        # Build gate → evaluator (proceed) or builder (reloop, max 3)
        Edge(source="gate_build", target="evaluator", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Evaluator → precheck gate
        Edge(source="evaluator", target="gate_precheck"),
        # Precheck → archivist (proceed) or halt
        Edge(source="gate_precheck", target="archivist_build", condition=VerdictType.PROCEED),
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
        return (
            state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE}
            and ctx.get("interactive", False)
        )

    wf.trigger = trigger
    return wf


# ── W₃: Improve Mode ────────────────────────────────────────────


def improve_workflow() -> Workflow:
    """W₃: Improve Mode — study → research → strategy → per-hypothesis build/eval loop.

    Study → Researcher → CEO gate → Strategist → CEO gate →
    per-hypothesis: begin → Builder → CEO gate(max 3) → Evaluator → Precheck →
    finalize → Archivist(async)
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

    # Per-hypothesis: begin → builder → gate → evaluator → precheck → finalize → archivist
    nodes["begin"] = FnNode(
        id="begin",
        command='factory begin {project_path} --hypothesis "Implement hypothesis"',
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

    nodes["evaluator"] = AgentNode(
        id="evaluator",
        role=AgentRole.EVALUATOR,
        prompt_template=(
            "Run eval: factory eval $PROJECT_PATH. "
            "Capture composite score. Report delta from baseline. "
            "Interpret dimension changes."
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/evaluator-latest.md"},
    )

    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/evaluator-latest.md"},
    )

    nodes["finalize"] = FnNode(
        id="finalize",
        command="factory finalize {project_path} --id 1 --verdict keep --hypothesis 'hypothesis'",
        reads={".factory/reviews/evaluator-latest.md"},
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
        # Strategy gate
        Edge(source="gate_strategy", target="begin", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # begin → builder
        Edge(source="begin", target="builder"),
        # Builder → build gate
        Edge(source="builder", target="gate_build"),
        # Build gate
        Edge(source="gate_build", target="evaluator", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Evaluator → precheck
        Edge(source="evaluator", target="gate_precheck"),
        # Precheck → finalize (proceed) or halt
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        # Finalize → archivist
        Edge(source="finalize", target="archivist"),
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
             evaluator ← research_command, + plateau_gate]
    """
    wf = improve_workflow()

    # Replace study with baseline measurement
    del wf.nodes["study"]

    wf.nodes["baseline"] = FnNode(
        id="baseline",
        command="factory eval {project_path}",
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

    # Replace evaluator with research command
    wf.nodes["evaluator"] = FnNode(
        id="evaluator",
        command="factory eval {project_path}",
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/evaluator-latest.md"},
    )

    # Add plateau gate after finalize — checks if score improved over prior runs
    wf.nodes["plateau_gate"] = GateNode(
        id="plateau_gate",
        evaluator_type="fn",
        evaluator_command=(
            "python3 -c \""
            "import json, pathlib, sys; "
            "tsv = pathlib.Path('{project_path}/.factory/results.tsv'); "
            "lines = [l for l in tsv.read_text().strip().splitlines()[1:] if l.strip()] if tsv.exists() else []; "
            "scores = []; "
            "[scores.append(float(p)) for l in lines for i, p in enumerate(l.split(chr(9))) if i == 2 and p]; "
            "recent = scores[-3:] if len(scores) >= 3 else scores; "
            "improved = len(recent) < 2 or recent[-1] > recent[-2]; "
            "print('RELOOP' if improved else 'PROCEED')"
            "\""
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
        # Strategist → strategy gate
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="begin", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # begin → builder
        Edge(source="begin", target="builder"),
        # Builder → build gate
        Edge(source="builder", target="gate_build"),
        Edge(source="gate_build", target="evaluator", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Evaluator → precheck
        Edge(source="evaluator", target="gate_precheck"),
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        # Finalize → archivist → plateau gate
        Edge(source="finalize", target="archivist"),
        Edge(source="archivist", target="plateau_gate"),
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
    Archivist(async) → test_collect → test_researcher → gate → test_builder

    The archivist is non-blocking, so it fires in the background while the
    test pruning chain proceeds immediately.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Collect cross-project insights
    nodes["insights"] = FnNode(
        id="insights",
        command="factory insights {project_path}",
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
        prompt_template=(
            "Delete the approved redundant tests. "
            "Verify remaining suite still passes."
        ),
        reads={".factory/strategy/test-analysis.md"},
        writes={".factory/reviews/test-pruning-latest.md"},
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


# ── Registry ─────────────────────────────────────────────────────


def register_all() -> dict[str, Workflow]:
    """Build and return all 5 workflow definitions."""
    return {
        "build": build_workflow(),
        "design": design_workflow(),
        "improve": improve_workflow(),
        "research": research_workflow(),
        "meta": meta_workflow(),
    }
