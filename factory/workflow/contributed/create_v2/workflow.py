"""create-v2: Create mode with inference-time scaling.

Dynamic research, multi-strategy, user intent tracking,
QA Director with workflow-specific testing, and Overwatch verification.
"""

from __future__ import annotations

from factory.workflow.definitions import _study_subgraph, create_workflow
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
)

from .prompts import (
    CREATE_GATE_OVERWATCH_PROMPT,
    CREATE_GATE_QA_PROMPT,
    CREATE_GATE_STRATEGY_PROMPT,
    CREATE_OVERWATCH_PROMPT,
    CREATE_QA_DIRECTOR_PROMPT,
    CREATE_RESEARCH_DIRECTOR_PROMPT,
    CREATE_STRATEGY_DIRECTOR_PROMPT,
    CREATE_SYNTHESIZE_STRATEGY_PROMPT,
)

meta = {
    "name": "create-v2",
    "description": (
        "Create mode with inference-time scaling: dynamic research, "
        "multi-strategy, user intent tracking, workflow-specific QA, "
        "and Overwatch verification for building new factory modes"
    ),
}


def workflow():
    """Build the create-v2 workflow graph."""
    wf = create_workflow()

    # ── Bootstrap: gate_has_factory + discover + factory.md creation ──

    wf.nodes["gate_has_factory"] = GateNode(
        id="gate_has_factory",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from pathlib import Path; "
            'exists = Path("{project_path}/.factory/config.json").exists(); '
            'print("PROCEED" if exists else "HALT")'
            '"'
        ),
    )

    wf.nodes["discover"] = FnNode(
        id="discover",
        command="factory discover {project_path}",
        writes={".factory/eval_profile.json"},
    )

    wf.nodes["gate_factory_md_exists"] = GateNode(
        id="gate_factory_md_exists",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from pathlib import Path; "
            'exists = Path("{project_path}/factory.md").exists(); '
            'print("PROCEED" if exists else "HALT")'
            '"'
        ),
    )

    wf.nodes["create_factory_md"] = AgentNode(
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

    wf.nodes["factory_init"] = FnNode(
        id="factory_init",
        command="factory init {project_path}",
        notes=(
            "Parse factory.md and generate .factory/config.json. "
            "Must run after factory.md is created."
        ),
        reads={"factory.md"},
        writes={".factory/config.json"},
    )

    # ── Study subgraph ──

    s_nodes, s_edges = _study_subgraph()
    wf.nodes.update(s_nodes)

    # ── User intent init (NEW) ──

    wf.nodes["init_user_intent"] = FnNode(
        id="init_user_intent",
        command=(
            'python3 -c '
            '"from factory.workflow.contributed.create_v2.intent_init '
            'import main; main()" '
            '"{project_path}"'
        ),
        writes={".factory/strategy/user-intent.md"},
        notes="Creates the user intent ledger with the initial mode description.",
    )

    # ── Research Director (NEW — replaces fork/join research) ──

    wf.nodes["research_director"] = AgentNode(
        id="research_director",
        role=AgentRole.CEO,
        timeout=3600,
        prompt_template=CREATE_RESEARCH_DIRECTOR_PROMPT,
        reads={
            ".factory/strategy/study-combined.md",
            ".factory/strategy/user-intent.md",
        },
        writes={".factory/strategy/research-plan.json"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-plan.json",
                must_exist=True,
                min_size=20,
            ),
        ],
    )

    # ── Strategy Director (NEW — replaces single strategist) ──

    wf.nodes["strategy_director"] = AgentNode(
        id="strategy_director",
        role=AgentRole.CEO,
        timeout=3600,
        prompt_template=CREATE_STRATEGY_DIRECTOR_PROMPT,
        reads={
            ".factory/strategy/research-plan.json",
            ".factory/strategy/user-intent.md",
            ".factory/strategy/study-combined.md",
        },
        writes={".factory/strategy/strategy-plan.json"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/strategy-plan.json",
                must_exist=True,
                min_size=20,
            ),
        ],
    )

    # ── Synthesize Strategy (NEW) ──

    wf.nodes["synthesize_strategy"] = AgentNode(
        id="synthesize_strategy",
        role=AgentRole.STRATEGIST,
        prompt_template=CREATE_SYNTHESIZE_STRATEGY_PROMPT,
        reads={
            ".factory/strategy/user-intent.md",
            ".factory/strategy/strategy-plan.json",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=200,
                must_contain=[
                    "### Graph Topology",
                    "### Node Definitions",
                ],
            ),
        ],
    )

    # ── QA Director (NEW — replaces static adversarial tester) ──

    wf.nodes["qa_director"] = AgentNode(
        id="qa_director",
        role=AgentRole.CEO,
        timeout=3600,
        prompt_template=CREATE_QA_DIRECTOR_PROMPT,
        reads={
            ".factory/strategy/current.md",
            ".factory/strategy/user-intent.md",
            ".factory/reviews/builder-latest.md",
        },
        writes={".factory/reviews/qa-plan.json"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/qa-plan.json",
                must_exist=True,
                min_size=20,
            ),
        ],
    )

    # ── Synthesize QA (NEW — glob-based merge of adversarial reports) ──

    wf.nodes["synthesize_qa"] = FnNode(
        id="synthesize_qa",
        command=(
            "python3 -c "
            '"from factory.workflow.contributed.create_v2.qa_synthesis '
            'import main; main()" '
            '"{project_path}"'
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
        },
        writes={".factory/reviews/qa-synthesized.md"},
        notes=(
            "Merges ALL adversarial-*-latest.md reports into one synthesized "
            "QA report. Uses glob pattern — works for any K testers. "
            "Findings caught by 2+ testers = HIGH confidence. "
            "Single-source findings surfaced but marked. "
            "Health checker and code reviewer reports included as pass-through."
        ),
    )

    # ── Modify existing nodes ──

    wf.nodes["fork_qa"] = ForkNode(
        id="fork_qa",
        targets=["health_checker", "code_reviewer", "qa_director"],
    )

    wf.nodes["join_qa"] = JoinNode(
        id="join_qa",
        sources=["health_checker", "code_reviewer", "qa_director"],
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/qa-plan.json",
        },
    )

    wf.nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        reads={
            ".factory/strategy/current.md",
            ".factory/strategy/user-intent.md",
        },
        gate_prompt=CREATE_GATE_STRATEGY_PROMPT,
    )

    wf.nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=CREATE_GATE_QA_PROMPT,
        reads={
            ".factory/strategy/user-intent.md",
            ".factory/reviews/qa-synthesized.md",
        },
    )

    # ── Overwatch (NEW — final verification before finalization) ──

    wf.nodes["overwatch"] = AgentNode(
        id="overwatch",
        role=AgentRole.CEO,
        timeout=1800,
        prompt_template=CREATE_OVERWATCH_PROMPT,
        reads={
            ".factory/strategy/user-intent.md",
            ".factory/strategy/current.md",
            ".factory/reviews/builder-latest.md",
            ".factory/reviews/qa-synthesized.md",
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
        },
        writes={".factory/reviews/overwatch-latest.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/overwatch-latest.md",
                must_exist=True,
                min_size=100,
            ),
        ],
    )

    wf.nodes["gate_overwatch"] = GateNode(
        id="gate_overwatch",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=CREATE_GATE_OVERWATCH_PROMPT,
        reads={
            ".factory/reviews/overwatch-latest.md",
            ".factory/strategy/user-intent.md",
        },
    )

    # ── Fix inherited node reads for create-v2 data flow ──
    for nid in ("gate_doc_freshness", "gate_precheck", "archivist_build"):
        node = wf.nodes[nid]
        new_reads = (node.reads - {".factory/reviews/adversarial-qa.md"}) | {
            ".factory/reviews/qa-synthesized.md"
        }
        wf.nodes[nid] = node.model_copy(update={"reads": new_reads})

    # ── Remove old nodes ──

    _removed = {
        "fork_research",
        "researcher_existing",
        "researcher_intent",
        "researcher_practices",
        "join_research",
        "gate_research",
        "strategist",
        "adversarial_tester",
    }
    for nid in _removed:
        wf.nodes.pop(nid, None)

    # ── Rebuild edges ──

    wf.edges = [
        e
        for e in wf.edges
        if e.source not in _removed
        and e.target not in _removed
        and not (e.source == "join_qa" and e.target == "gate_qa")
        and not (e.source == "gate_qa" and e.target == "gate_doc_freshness")
    ]

    # Bootstrap + study edges
    wf.edges.extend(
        [
            *s_edges,
            Edge(
                source="gate_has_factory",
                target="graph_update",
                condition=VerdictType.PROCEED,
            ),
            Edge(
                source="gate_has_factory",
                target="discover",
                condition=VerdictType.HALT,
            ),
            Edge(source="discover", target="gate_factory_md_exists"),
            Edge(
                source="gate_factory_md_exists",
                target="factory_init",
                condition=VerdictType.PROCEED,
            ),
            Edge(
                source="gate_factory_md_exists",
                target="create_factory_md",
                condition=VerdictType.HALT,
            ),
            Edge(source="create_factory_md", target="factory_init"),
            Edge(source="factory_init", target="graph_update"),
        ]
    )

    # Create-v2 specific edges
    wf.edges.extend(
        [
            Edge(source="init_user_intent", target="gate_has_factory"),
            Edge(source="concat_study", target="research_director"),
            Edge(source="research_director", target="strategy_director"),
            Edge(source="strategy_director", target="synthesize_strategy"),
            Edge(source="synthesize_strategy", target="gate_strategy"),
            Edge(
                source="gate_strategy",
                target="strategy_director",
                condition=VerdictType.RELOOP,
            ),
            Edge(source="join_qa", target="synthesize_qa"),
            Edge(source="synthesize_qa", target="gate_qa"),
            Edge(
                source="gate_qa",
                target="overwatch",
                condition=VerdictType.PROCEED,
            ),
            Edge(source="overwatch", target="gate_overwatch"),
            Edge(
                source="gate_overwatch",
                target="gate_doc_freshness",
                condition=VerdictType.PROCEED,
            ),
            Edge(
                source="gate_overwatch",
                target="builder",
                condition=VerdictType.RELOOP,
            ),
        ]
    )

    # ── Set workflow metadata ──

    wf.start_node = "init_user_intent"
    wf.name = "create-v2"
    wf.terminal = True

    wf.validate_graph()

    return wf
