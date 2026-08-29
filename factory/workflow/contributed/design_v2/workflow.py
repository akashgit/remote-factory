"""design-v2: Design mode with inference-time scaling.

Dynamic research, multi-strategy, user intent tracking,
and QA Director with tailored adversarial testing.
"""

from __future__ import annotations

from factory.workflow.definitions import _study_subgraph, build_workflow
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

from .prompts import (
    DESIGN_DOC_PROMPT,
    GATE_QA_PROMPT,
    QA_DIRECTOR_PROMPT,
    RESEARCH_DIRECTOR_PROMPT,
    STRATEGY_DIRECTOR_PROMPT,
    SYNTHESIZE_STRATEGY_PROMPT,
)

meta = {
    "name": "design-v2",
    "description": (
        "Design mode with inference-time scaling: dynamic research, "
        "multi-strategy, user intent tracking, parallel adversarial QA"
    ),
}


def workflow() -> Workflow:
    """Build the design-v2 workflow graph."""
    wf = build_workflow()

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
            "python3 -c \""
            "import datetime, os, sys; "
            "from pathlib import Path; "
            "project = sys.argv[1]; "
            "intent = Path(f'{project}/.factory/strategy/user-intent.md'); "
            "(sys.exit(0) if intent.exists() and intent.stat().st_size > 0 else None); "
            "ts = datetime.datetime.now().isoformat(timespec='seconds'); "
            "bl = Path(f'{project}/.factory/strategy/backlog.md'); "
            "idea = os.environ.get('FACTORY_IDEA', '') "
            "or (bl.read_text().strip().splitlines()[0] "
            "if bl.exists() and bl.stat().st_size > 0 else '') "
            "or 'No idea provided'; "
            "Path(f'{project}/.factory/strategy').mkdir(parents=True, exist_ok=True); "
            "content = f'# User Intent Ledger\\\\n\\\\n## [{ts}] Initial Idea\\\\n{idea}\\\\n'; "
            "intent.write_text(content); "
            "print(f'User intent ledger initialized at {ts}')\" "
            "\"{project_path}\""
        ),
        writes={".factory/strategy/user-intent.md"},
        notes="Creates the user intent ledger with the initial idea.",
    )

    # ── Research Director (NEW — replaces fork/join research) ──

    wf.nodes["research_director"] = AgentNode(
        id="research_director",
        role=AgentRole.CEO,
        timeout=3600,
        prompt_template=RESEARCH_DIRECTOR_PROMPT,
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
        prompt_template=STRATEGY_DIRECTOR_PROMPT,
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
        prompt_template=SYNTHESIZE_STRATEGY_PROMPT,
        reads={".factory/strategy/user-intent.md"},
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=200,
                must_contain=[
                    "### Phased Plan",
                    "### Acceptance Criteria",
                ],
            ),
        ],
    )

    # ── Design Doc (NEW) ──

    wf.nodes["design_doc"] = AgentNode(
        id="design_doc",
        role=AgentRole.STRATEGIST,
        prompt_template=DESIGN_DOC_PROMPT,
        reads={
            ".factory/strategy/current.md",
            ".factory/strategy/user-intent.md",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=500,
                must_contain=[
                    "## What We're Building",
                    "## Architecture",
                    "## How It Works",
                    "## Acceptance Criteria",
                ],
            ),
        ],
    )

    # ── QA Director (NEW — replaces static adversarial tester) ──

    wf.nodes["qa_director"] = AgentNode(
        id="qa_director",
        role=AgentRole.CEO,
        timeout=3600,
        prompt_template=QA_DIRECTOR_PROMPT,
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
            "python3 -c \""
            "from pathlib import Path; "
            "import re, sys; "
            "project = sys.argv[1]; "
            "reports = []; "
            "for p in sorted(Path(f'{project}/.factory/reviews').glob("
            "'adversarial-*-latest.md')): "
            "    slug = p.name.replace('-latest.md', '').replace("
            "'adversarial-', ''); "
            "    reports.append((slug, p.read_text())); "
            "findings = {}; "
            "for tester_slug, text in reports: "
            "    for line in text.splitlines(): "
            "        stripped = line.strip(); "
            "        if stripped.startswith('- ') or stripped.startswith('* '): "
            "            key = re.sub(r'\\\\s+', ' ', "
            "stripped[2:].strip().lower()[:80]); "
            "            findings.setdefault(key, []).append(tester_slug); "
            "high = [(k, v) for k, v in findings.items() if len(v) >= 2]; "
            "medium = [(k, v) for k, v in findings.items() if len(v) == 1]; "
            "out = ['# Synthesized QA Report\\\\n']; "
            "hc = Path(f'{project}/.factory/reviews/health-check.md'); "
            "cr = Path(f'{project}/.factory/reviews/code-review.md'); "
            "out.append('## Health Check\\\\n'); "
            "out.append(hc.read_text() if hc.exists() else '(not available)'); "
            "out.append('\\\\n## Code Review\\\\n'); "
            "out.append(cr.read_text() if cr.exists() else '(not available)'); "
            "out.append('\\\\n## High-Confidence Adversarial Findings "
            "(caught by 2+ testers)\\\\n'); "
            "[out.append(f'- {k} (testers: {v})') for k, v in high]; "
            "if not high: out.append('- (none)'); "
            "out.append('\\\\n## Medium-Confidence Adversarial Findings "
            "(single tester)\\\\n'); "
            "[out.append(f'- {k} (tester: {v[0]})') for k, v in medium]; "
            "if not medium: out.append('- (none)'); "
            "out.append('\\\\n## Raw Adversarial Reports\\\\n'); "
            "[out.append(f'### Tester: {slug}\\\\n{text}\\\\n') "
            "for slug, text in reports]; "
            "Path(f'{project}/.factory/reviews/qa-synthesized.md').write_text("
            "'\\\\n'.join(out)); "
            "print(f'Synthesized {len(high)} high + {len(medium)} medium "
            "findings from {len(reports)} adversarial reports')\" "
            "\"{project_path}\""
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
        gate_prompt=(
            "Review the design document at .factory/strategy/current.md. "
            "This is a human-readable design document explaining what will be "
            "built, how the architecture works, and the acceptance criteria "
            "for completion. "
            "Compare against the user's original intent in user-intent.md. "
            "On REVISE: append your feedback to "
            ".factory/strategy/user-intent.md "
            "under a new '## [timestamp] Feedback at Strategy Gate' heading "
            "before relooping to strategy_director."
        ),
    )

    wf.nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=GATE_QA_PROMPT,
        reads={
            ".factory/strategy/user-intent.md",
            ".factory/reviews/qa-synthesized.md",
        },
    )

    # ── Fix inherited node reads for design-v2 data flow ──
    # These nodes inherited reads of adversarial-qa.md from build_workflow,
    # but design-v2 replaces that with qa-synthesized.md.
    for nid in ("gate_doc_freshness", "gate_precheck", "archivist_build"):
        node = wf.nodes[nid]
        new_reads = (node.reads - {".factory/reviews/adversarial-qa.md"}) | {
            ".factory/reviews/qa-synthesized.md"
        }
        wf.nodes[nid] = node.model_copy(update={"reads": new_reads})

    # ── Remove old nodes ──

    _removed = {
        "fork_research",
        "researcher_similar",
        "researcher_techstack",
        "researcher_pitfalls",
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

    # Design-v2 specific edges
    wf.edges.extend(
        [
            Edge(source="init_user_intent", target="gate_has_factory"),
            Edge(source="concat_study", target="research_director"),
            Edge(source="research_director", target="strategy_director"),
            Edge(source="strategy_director", target="synthesize_strategy"),
            Edge(source="synthesize_strategy", target="design_doc"),
            Edge(source="design_doc", target="gate_strategy"),
            Edge(
                source="gate_strategy",
                target="strategy_director",
                condition=VerdictType.RELOOP,
            ),
            Edge(source="join_qa", target="synthesize_qa"),
            Edge(source="synthesize_qa", target="gate_qa"),
        ]
    )

    # ── Set workflow metadata ──

    wf.start_node = "init_user_intent"
    wf.name = "design-v2"
    wf.terminal = True

    return wf
