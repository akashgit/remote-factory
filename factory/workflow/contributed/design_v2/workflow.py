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

meta = {
    "name": "design-v2",
    "description": (
        "Design mode with inference-time scaling: dynamic research, "
        "multi-strategy, user intent tracking, parallel adversarial QA"
    ),
}

# ── Prompt templates ────────────────────────────────────────────

RESEARCH_DIRECTOR_PROMPT = """\
You are the Research Director for this design session.

Read:
- `.factory/strategy/study-combined.md` — project study findings (observations + graph analysis)
- `.factory/strategy/user-intent.md` — the user's original idea

Your task has TWO phases:

PHASE 1 — DESIGN RESEARCH DIRECTIONS
Analyze the design space and identify N orthogonal research dimensions.
Default dimensions (adapt or replace based on the specific project):
  - Similar projects / prior art
  - Technology stack options
  - Common pitfalls and failure modes
You may add dimensions specific to the domain (e.g., security, UX patterns,
data model constraints, compliance requirements, API design, concurrency).

N is NOT fixed — YOU decide based on domain complexity:
  - Simple CLI or library: 3 directions
  - Web app with auth, database, UI: 4-5 directions
  - Complex system with integrations, security, compliance: 5-7 directions

For each direction, design a TAILORED prompt — not a generic template.
Bad: "Research similar projects and prior art"
Good: "Find existing link-checking tools that handle Obsidian-style wikilinks
       ([[note]]) and image embeds (![[image.png]]). Compare how they resolve
       relative paths vs vault-root-relative paths."

Write the research plan to `.factory/strategy/research-plan.json`:
```json
[
  {"focus": "...", "slug": "...", "prompt": "..."}
]
```

Constraints:
- Minimum 3 directions, maximum 7
- Each slug must be unique and kebab-case
- Prompts must be specific to THIS project, not generic templates

PHASE 2 — EXECUTE RESEARCH
For each direction in the plan, spawn a researcher agent:
```
factory agent researcher --task "<direction.prompt>" --project {project_path}
```

Each researcher writes to `.factory/strategy/research-<slug>.md`.

After ALL researchers complete, review quality:
- Each research file exists and has substantive content (>50 bytes)
- No two reports cover the same ground excessively
- Key risks and opportunities are covered

If a researcher produced thin output, re-invoke it with a more specific prompt.

Write a brief research summary to the end of research-plan.json noting
which directions completed and any quality issues."""

STRATEGY_DIRECTOR_PROMPT = """\
You are the Strategy Director for this design session.

Read:
- ALL research reports at `.factory/strategy/research-*.md`
- `.factory/strategy/research-plan.json` — which research directions were explored
- `.factory/strategy/user-intent.md` — the user's original idea
- `.factory/strategy/study-combined.md` — project context

Your task has TWO phases:

PHASE 1 — DESIGN STRATEGY PERSPECTIVES
Analyze the research findings and user intent to identify M strategy
perspectives this project needs.

Default perspectives (adapt or replace based on the specific project):
  - Architecture strategy — how to build it (components, phases, tech choices)
  - Testing/verification strategy — how to verify it works (acceptance criteria,
    test plan, edge cases)
  - Risk/scope strategy — what to cut, what's hard, what breaks

You may add perspectives specific to the domain:
  - Security strategy (for auth-heavy projects)
  - Data modeling strategy (for data-heavy projects)
  - API design strategy (for API-first projects)
  - Performance strategy (for latency-sensitive systems)

M is NOT fixed — YOU decide based on project complexity:
  - Simple project: 2-3 perspectives
  - Medium project: 3-4 perspectives
  - Complex project: 4-5 perspectives

For each perspective, design a TAILORED prompt.
Bad: "Create an architecture strategy"
Good: "Design the architecture for a markdown link checker CLI. The core
       challenge is resolving Obsidian wikilinks against a configurable vault
       root while also supporting standard URLs with redirect following.
       Research shows <X library> handles HTTP well but nothing handles
       wikilinks — design that component from scratch."

Write the strategy plan to `.factory/strategy/strategy-plan.json`:
```json
[
  {"perspective": "...", "slug": "...", "prompt": "..."}
]
```

Constraints:
- Minimum 2 perspectives, maximum 5
- Each slug must be unique and kebab-case
- One perspective MUST cover testing/verification with explicit acceptance criteria
- Prompts must reference specific findings from the research reports

PHASE 2 — EXECUTE STRATEGIES
For each perspective in the plan, spawn a strategist agent:
```
factory agent strategist --task "<perspective.prompt>" --project {project_path}
```

Each strategist writes to `.factory/strategy/strategy-<slug>.md`.

After ALL strategists complete, review quality:
- Each strategy file exists and has substantive content (>100 bytes)
- The testing strategy has a `### Acceptance Criteria` section with checkboxes
- Architecture strategy cites research findings
- No critical perspective is missing

If a strategist produced thin output, re-invoke it with a more specific prompt.

Write a brief strategy summary to the end of strategy-plan.json noting
which perspectives completed and any quality issues."""

SYNTHESIZE_STRATEGY_PROMPT = """\
You are the Strategy Synthesizer. Compile one final plan from all
strategy inputs.

Read:
- ALL strategy files at `.factory/strategy/strategy-*.md`
- `.factory/strategy/strategy-plan.json` — which perspectives were explored
- `.factory/strategy/user-intent.md` — ground truth for user's ask

Write the final plan to `.factory/strategy/current.md`.

Required sections (in this order):
### Architecture
  Merge from architecture strategy. Include component list and interfaces.
### Phased Plan
  #### Phase 1: <name>
  - **What:** <specific changes from architecture strategy>
  - **Why:** <rationale>
  - **Acceptance criteria:** <from testing strategy — inline the relevant criteria>
  - **Risks:** <from risk strategy — inline relevant risks>
  #### Phase 2: <name>
  ...
### Acceptance Criteria
  Full checklist from testing strategy. Each item must be:
  - [ ] Specific enough for pass/fail verification
  - Traceable to user intent (cite which part of user-intent.md)
### MVP Scope
  From risk strategy. In vs deferred.
### Deferred Features
  Items requiring human intervention or explicitly deferred.

CRITICAL: The ### Acceptance Criteria section is the contract between
the builder and QA. It flows to adversarial testers who verify each
criterion independently. Make every item testable and unambiguous."""

DESIGN_DOC_PROMPT = """\
You are a Technical Writer and Design Architect. Your job: take the structured
strategy at `.factory/strategy/current.md` and rewrite it as a proper
DESIGN DOCUMENT — a document a human reviewer can read end-to-end and
understand exactly what is being built, why, and how.

Read:
- `.factory/strategy/current.md` — the structured strategy (your raw input)
- `.factory/strategy/user-intent.md` — the user's original idea and feedback

Rewrite `.factory/strategy/current.md` IN PLACE. Replace the compressed
bullet points with a well-structured design document.

Required sections (in this order):

## What We're Building
  Explain the project in 2-3 paragraphs of prose. What does it do?
  Who is it for? What problem does it solve? Reference the user's
  original words from user-intent.md.

## Architecture
  Describe the system architecture in full sentences.
  Include a text-based architecture diagram using box-drawing characters:
  ```
  ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ Component│────▶│ Component│────▶│ Component│
  └──────────┘     └──────────┘     └──────────┘
  ```
  Explain each component — what it does, why it exists, how it connects.

## How It Works
  Walk through the user flow step by step. For a CLI, show example
  invocations and expected output. For a web app, describe the user
  journey screen by screen. For a library, show usage examples.

  This should read like a tutorial — someone unfamiliar with the project
  should be able to follow along.

## Phased Plan
  For each phase, explain:
  - What gets built in this phase and why this ordering
  - What the user can do after this phase completes
  - How to verify the phase worked (concrete test commands or checks)

  Use prose paragraphs, not just bullet points. Each phase should
  read as a self-contained "chapter."

## Acceptance Criteria
  Present the full checklist, but group criteria by category and add
  context for each. Explain WHY each criterion matters, not just what it is.

  Format: category heading, then checkbox items with brief explanation.

## MVP Scope
  What's in, what's deferred, and why. Explain the tradeoffs.

## Deferred Features
  Items deferred to future phases, with brief rationale.

CRITICAL RULES:
- Write in full sentences and paragraphs, NOT bullet points
- The reader should understand the design WITHOUT reading any other file
- Include concrete examples (CLI invocations, API calls, code snippets)
- Architecture diagrams must use text/box-drawing characters
- Every technical choice must be explained — no unexplained jargon
- The document must be self-contained: a human reviewer reads ONLY this
  file and decides whether to approve the design"""

QA_DIRECTOR_PROMPT = """\
You are the QA Director for this design session.

Read:
- `.factory/strategy/current.md` — the design document with acceptance criteria
- `.factory/strategy/user-intent.md` — what the user ACTUALLY asked for
- `.factory/reviews/builder-latest.md` — what the builder implemented

Your task has TWO phases:

PHASE 1 — DESIGN QA APPROACHES
Analyze the acceptance criteria, the user's intent, and the builder's
implementation to identify K orthogonal testing approaches.

Default approaches (adapt or replace based on the specific project):
  - Happy path verification — does each acceptance criterion pass with
    normal, expected inputs?
  - Edge case & boundary testing — empty inputs, large inputs, special
    characters, off-by-one, boundary conditions
  - User intent verification — does the output match what the user
    ACTUALLY asked for (from user-intent.md), not just the plan?

You may add approaches specific to the implementation:
  - Security testing (for auth, file I/O, user-facing APIs)
  - Integration testing (for multi-component systems)
  - Performance testing (for latency-sensitive features)
  - Error handling testing (for systems with many failure modes)
  - Concurrency testing (for parallel/async systems)

K is NOT fixed — YOU decide based on the complexity of the acceptance
criteria and the nature of the implementation:
  - Simple implementation (3-5 criteria): 2 testers
  - Medium implementation (5-10 criteria): 3 testers
  - Complex implementation (10+ criteria, security, integrations): 4-5 testers

For each approach, design a TAILORED prompt — not a generic template.
Bad: "Test the implementation for edge cases"
Good: "Test the Obsidian wikilink resolver with these edge cases:
       nested vault folders (vault/sub/note.md linking to ../other.md),
       wikilinks with display text ([[note|display]]),
       wikilinks to non-existent files, wikilinks with anchor
       fragments ([[note#heading]]), and case-sensitivity mismatches."

Write the QA plan to `.factory/reviews/qa-plan.json`:
```json
[
  {"approach": "...", "slug": "...", "prompt": "..."}
]
```

Constraints:
- Minimum 2 approaches, maximum 5
- Each slug must be unique and kebab-case
- ALL acceptance criteria from current.md must be covered by at least
  one tester's prompt
- One approach MUST verify user intent against user-intent.md
- Prompts must reference specific acceptance criteria and implementation details

PHASE 2 — EXECUTE QA
For each approach in the plan, spawn an adversarial tester agent:
```
factory agent adversarial_tester --task "<approach.prompt>" --project {project_path}
```

Each tester writes to `.factory/reviews/adversarial-<slug>-latest.md`.

After ALL testers complete, review quality:
- Each adversarial report exists and has substantive findings
- All acceptance criteria are covered by at least one tester
- No tester missed its assigned focus area
- Critical findings are actually reproducible (spot-check)

If a tester produced thin output, re-invoke it with a more specific prompt.

Write a brief QA summary to the end of qa-plan.json noting which
approaches completed and any quality issues."""

ADVERSARIAL_PROMPT = """\
You are an adversarial tester. Your job: break the implementation.

Read:
- `.factory/strategy/current.md` — the design document and acceptance criteria
- `.factory/strategy/user-intent.md` — what the user ACTUALLY asked for
- `.factory/reviews/builder-latest.md` — builder's work summary
- Source code changes (use git diff)

Procedure:
1. Read the acceptance criteria from current.md
2. For each criterion, attempt to verify it by running the code
3. Try edge cases, invalid inputs, boundary conditions
4. Check that the implementation matches user intent, not just the plan
5. Look for security issues, error handling gaps, missing validations

Output format:
# Adversarial Test Report

## Acceptance Criteria Verification
- [ ] Criterion 1: PASS/FAIL — evidence
- [ ] Criterion 2: PASS/FAIL — evidence

## Edge Case Findings
- Finding: <description>
  - Steps to reproduce: <steps>
  - Expected: <behavior>
  - Actual: <behavior>

## User Intent Verification
- Does the output match what the user asked for? Evidence: <...>"""

GATE_QA_PROMPT = """\
You are the CEO reviewing QA results. This is the final gate before merge.

Read:
- `.factory/strategy/user-intent.md` — what the user ACTUALLY asked for
- `.factory/reviews/qa-synthesized.md` — merged QA report (health check,
  code review, and synthesized adversarial findings)

Decision framework:

PROCEED if ALL of these hold:
  1. All acceptance criteria from current.md are verified PASS
  2. Health check passes (tests green, eval not regressed)
  3. No blocking code review issues
  4. No HIGH-confidence adversarial findings that violate user intent

RELOOP to builder (max 3 iterations) if ANY of these hold:
  1. An acceptance criterion failed — cite which one
  2. Health check failed — cite which check
  3. Blocking review or HIGH adversarial findings

  When relooping, provide feedback mapped to SPECIFIC user requirements:
  - "User asked for X (user-intent.md), but <finding from QA>"
  - "Acceptance criterion '<criterion>' FAILED: <evidence>"

  IMPORTANT: Append your reloop feedback to .factory/strategy/user-intent.md
  under a new '## [timestamp] Reloop Feedback (Iteration N)' heading.

HALT if:
  - 3 reloops exhausted without resolution
  - Fundamental design flaw that builder iterations cannot fix"""


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
            'python3 -c "'
            "import datetime, os; "
            "project = '{project_path}'; "
            "ts = datetime.datetime.now().isoformat(timespec='seconds'); "
            "idea = os.environ.get('FOCUS', os.environ.get('FACTORY_IDEA', "
            "'No idea provided')); "
            "content = f'# User Intent Ledger\\n\\n## [{ts}] Initial Idea\\n"
            "{idea}\\n'; "
            "open(f'{project}/.factory/strategy/user-intent.md', 'w').write("
            "content); "
            "print(f'User intent ledger initialized at {ts}')"
            '"'
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
            'python3 -c "'
            "from pathlib import Path; "
            "import re, glob; "
            "project = '{project_path}'; "
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
            "            key = re.sub(r'\\s+', ' ', "
            "stripped[2:].strip().lower()[:80]); "
            "            findings.setdefault(key, []).append(tester_slug); "
            "high = [(k, v) for k, v in findings.items() if len(v) >= 2]; "
            "medium = [(k, v) for k, v in findings.items() if len(v) == 1]; "
            "out = ['# Synthesized QA Report\\n']; "
            "hc = Path(f'{project}/.factory/reviews/health-check.md'); "
            "cr = Path(f'{project}/.factory/reviews/code-review.md'); "
            "out.append('## Health Check\\n'); "
            "out.append(hc.read_text() if hc.exists() else '(not available)'); "
            "out.append('\\n## Code Review\\n'); "
            "out.append(cr.read_text() if cr.exists() else '(not available)'); "
            "out.append('\\n## High-Confidence Adversarial Findings "
            "(caught by 2+ testers)\\n'); "
            "[out.append(f'- {k} (testers: {v})') for k, v in high]; "
            "if not high: out.append('- (none)'); "
            "out.append('\\n## Medium-Confidence Adversarial Findings "
            "(single tester)\\n'); "
            "[out.append(f'- {k} (tester: {v[0]})') for k, v in medium]; "
            "if not medium: out.append('- (none)'); "
            "out.append('\\n## Raw Adversarial Reports\\n'); "
            "[out.append(f'### Tester: {slug}\\n{text}\\n') "
            "for slug, text in reports]; "
            "Path(f'{project}/.factory/reviews/qa-synthesized.md').write_text("
            "'\\n'.join(out)); "
            "print(f'Synthesized {len(high)} high + {len(medium)} medium "
            "findings from {len(reports)} adversarial reports')"
            '"'
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
