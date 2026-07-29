"""W9b: Create Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions._shared import DOC_FRESHNESS_GATE_PROMPT, _deep_qa_subgraph
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
    Workflow,
)


def create_workflow() -> Workflow:
    """W9b: Create Mode — meta-mode for creating new factory modes.

    Takes a user description and produces a fully working workflow definition,
    SKILL.md, CLI wiring, and tests.

    Fork(3 researchers) -> Join -> CEO gate -> Strategist -> User gate ->
    Archivist(async) -> Builder -> CEO gate -> deep-QA -> gate_qa(max 3) ->
    Precheck gate -> Archivist(async)
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
            "node type usage patterns, common subgraphs (builder->gate->qa->gate loop), "
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
            "builder->gate->QA->gate loops, archivist placement, and research forks. "
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
            "Commit changes and open a draft PR."
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
        # Join -> research gate
        Edge(source="join_research", target="gate_research"),
        # Research gate
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="fork_research", condition=VerdictType.RELOOP),
        # Strategist -> user gate
        Edge(source="strategist", target="gate_strategy"),
        # User gate
        Edge(source="gate_strategy", target="archivist_plan", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # Archivist -> builder
        Edge(source="archivist_plan", target="builder"),
        # Builder -> build gate
        Edge(source="builder", target="gate_build"),
        # Build gate -> deep-qa (proceed) or builder (reloop)
        Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Deep-QA internal edges
        *dq_edges,
        # adversarial_tester -> gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        # gate_qa -> doc freshness (proceed) or builder (reloop)
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness -> precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck -> archivist (proceed) or halt -> archivist (error handling)
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
