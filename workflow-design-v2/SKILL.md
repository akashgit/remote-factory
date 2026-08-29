---
name: workflow-design-v2
description: "Run the design-v2 workflow."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Design V2 Workflow

The user wants: **$ARGUMENTS**

## Step: Init User Intent

Creates the user intent ledger with the initial idea.

```bash
python3 -c "import datetime, os; project = '$PROJECT_PATH'; ts = datetime.datetime.now().isoformat(timespec='seconds'); idea = os.environ.get('FOCUS', os.environ.get('FACTORY_IDEA', 'No idea provided')); content = f'# User Intent Ledger\n\n## [{ts}] Initial Idea\n{idea}\n'; open(f'{project}/.factory/strategy/user-intent.md', 'w').write(content); print(f'User intent ledger initialized at {ts}')"
```

### Gate — Has Factory (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
python3 -c "from pathlib import Path; exists = Path("$PROJECT_PATH/.factory/config.json").exists(); print("PROCEED" if exists else "HALT")"
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `graph_update`
- **HALT** (exit non-zero / FAIL in output) → continue to `discover` instead.

## Step: Discover

```bash
factory discover $PROJECT_PATH
```

### Gate — Factory Md Exists (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
python3 -c "from pathlib import Path; exists = Path("$PROJECT_PATH/factory.md").exists(); print("PROCEED" if exists else "HALT")"
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `factory_init`
- **HALT** (exit non-zero / FAIL in output) → continue to `create_factory_md` instead.

## Phase 1: Ceo — Create Factory Md

```bash
factory agent ceo --task "Create factory.md from template. Copy the factory config template to the project root. Fill in: Goal, Scope, Guards, Eval command, Threshold, and Smoke Test. If .factory/eval_spec.json exists, populate the Eval Spec section. If .factory/strategy/current.md has a Research Configuration section, populate research sections (Research Target, Mutable/Fixed Surfaces, etc.).
Read: .factory/eval_profile.json
Write output to: factory.md" --project "$PROJECT_PATH" --timeout 3600
```

```bash
# Artifact verification: create_factory_md
_vfail=0
_f="$PROJECT_PATH/factory.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: create_factory_md: factory.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: create_factory_md: factory.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=create_factory_md" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: create_factory_md artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=create_factory_md" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Factory Init

Parse factory.md and generate .factory/config.json. Must run after factory.md is created.

```bash
factory init $PROJECT_PATH
```

## Step: Graph Update

Extract or incrementally update the code knowledge graph before study.

```bash
factory graph update $PROJECT_PATH
```

## Phase 2: Observe

Run local study to gather observations:

```bash
factory study $PROJECT_PATH
```

Writes observations to `.factory/strategy/observations.md`.

If your task includes a focus directive or focus topic, pass it to the study command:
`factory study $PROJECT_PATH --focus "<your focus topic>"`

## Phase 3: Researcher — Graph Explorer

```bash
factory agent researcher --task "Explore the project's code knowledge graph to build structural understanding. Read .factory/strategy/observations.md for focus context.

**Step 0 — detect graph availability:** Your working directory is already the project root. The graph file lives at `$PROJECT_PATH/graph.json` (NOT inside `.factory/`). Run this smoke check FIRST — use a relative path since your CWD is the project root: `test -f graph.json && echo 'GRAPH AVAILABLE' || echo 'NO GRAPH'` — if the output says GRAPH AVAILABLE, proceed with the graph commands below. If the output says NO GRAPH, skip to the fallback section.

**If the graph IS available:**
1. Run `factory graph query "$PROJECT_PATH" "<focus from observations>" --depth 2` to find relevant nodes
2. Run `factory graph explain "$PROJECT_PATH" "<key node>"` on the most important nodes to understand their connections and dependencies
3. Run `factory graph path "$PROJECT_PATH" "<A>" "<B>"` to trace dependency paths between key components
4. Write structured findings to .factory/strategy/graph-context.md covering: key modules and their relationships, dependency paths, architectural layers, entry points and hotspots

**If the graph is NOT available**, fall back to direct file exploration:
1. Use `find . -name '*.py' | head -50` to discover source files
2. Use `grep -rn 'class \|def ' --include='*.py' | head -100` to map functions and classes
3. Use `grep -rn 'import ' --include='*.py' | head -100` to trace dependencies
4. Write the same structured findings to .factory/strategy/graph-context.md
Read: .factory/strategy/observations.md
Write output to: .factory/strategy/graph-context.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: graph_explorer
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/graph-context.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: graph_explorer: .factory/strategy/graph-context.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: graph_explorer: .factory/strategy/graph-context.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=graph_explorer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: graph_explorer artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=graph_explorer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Concat Study

```bash
cat $PROJECT_PATH/.factory/strategy/observations.md $PROJECT_PATH/.factory/strategy/graph-context.md > $PROJECT_PATH/.factory/strategy/study-combined.md
```

## Phase 4: Ceo — Research Director

```bash
factory agent ceo --task "You are the Research Director for this design session.

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
factory agent researcher --task "<direction.prompt>" --project $PROJECT_PATH
```

Each researcher writes to `.factory/strategy/research-<slug>.md`.

After ALL researchers complete, review quality:
- Each research file exists and has substantive content (>50 bytes)
- No two reports cover the same ground excessively
- Key risks and opportunities are covered

If a researcher produced thin output, re-invoke it with a more specific prompt.

Write a brief research summary to the end of research-plan.json noting
which directions completed and any quality issues.
Read: .factory/strategy/study-combined.md, .factory/strategy/user-intent.md
Write output to: .factory/strategy/research-plan.json" --project "$PROJECT_PATH" --timeout 3600
```

```bash
# Artifact verification: research_director
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/research-plan.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: research_director: .factory/strategy/research-plan.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: research_director: .factory/strategy/research-plan.json is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 20 ] && echo "VERIFY FAIL: research_director: .factory/strategy/research-plan.json smaller than 20 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=research_director" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: research_director artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=research_director" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 5: Ceo — Strategy Director

```bash
factory agent ceo --task "You are the Strategy Director for this design session.

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
factory agent strategist --task "<perspective.prompt>" --project $PROJECT_PATH
```

Each strategist writes to `.factory/strategy/strategy-<slug>.md`.

After ALL strategists complete, review quality:
- Each strategy file exists and has substantive content (>100 bytes)
- The testing strategy has a `### Acceptance Criteria` section with checkboxes
- Architecture strategy cites research findings
- No critical perspective is missing

If a strategist produced thin output, re-invoke it with a more specific prompt.

Write a brief strategy summary to the end of strategy-plan.json noting
which perspectives completed and any quality issues.
Read: .factory/strategy/research-plan.json, .factory/strategy/study-combined.md, .factory/strategy/user-intent.md
Write output to: .factory/strategy/strategy-plan.json" --project "$PROJECT_PATH" --timeout 3600
```

```bash
# Artifact verification: strategy_director
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/strategy-plan.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: strategy_director: .factory/strategy/strategy-plan.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: strategy_director: .factory/strategy/strategy-plan.json is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 20 ] && echo "VERIFY FAIL: strategy_director: .factory/strategy/strategy-plan.json smaller than 20 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=strategy_director" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: strategy_director artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=strategy_director" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 6: Strategist — Synthesize Strategy

```bash
factory agent strategist --task "You are the Strategy Synthesizer. Compile one final plan from all
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
criterion independently. Make every item testable and unambiguous.
Read: .factory/strategy/user-intent.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: synthesize_strategy
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/current.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: synthesize_strategy: .factory/strategy/current.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: synthesize_strategy: .factory/strategy/current.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 200 ] && echo "VERIFY FAIL: synthesize_strategy: .factory/strategy/current.md smaller than 200 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE '\#\#\#\ Phased\ Plan|\#\#\#\ Acceptance\ Criteria' "$_f" && echo "VERIFY FAIL: synthesize_strategy: .factory/strategy/current.md missing required sentinel (### Phased Plan, ### Acceptance Criteria)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=synthesize_strategy" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: synthesize_strategy artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=synthesize_strategy" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 7: Strategist — Design Doc

```bash
factory agent strategist --task "You are a Technical Writer and Design Architect. Your job: take the structured
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
  file and decides whether to approve the design
Read: .factory/strategy/current.md, .factory/strategy/user-intent.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: design_doc
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/current.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: design_doc: .factory/strategy/current.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: design_doc: .factory/strategy/current.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 500 ] && echo "VERIFY FAIL: design_doc: .factory/strategy/current.md smaller than 500 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE '\#\#\ What\ We're\ Building|\#\#\ Architecture|\#\#\ How\ It\ Works|\#\#\ Acceptance\ Criteria' "$_f" && echo "VERIFY FAIL: design_doc: .factory/strategy/current.md missing required sentinel (## What We're Building, ## Architecture, ## How It Works, ## Acceptance Criteria)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=design_doc" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: design_doc artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=design_doc" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Steering Point — Strategy (User Approval)

**This is a USER approval gate, NOT a CEO review gate. Do NOT self-approve.**

Present the strategy/findings to the user by summarizing key points in your output.
Then explicitly ask the user: "Do you approve this plan, or do you have feedback?"

**You MUST wait for the user's response before proceeding.**
- The user says "approve", "yes", "looks good", or similar → proceed to next step
- The user provides feedback or corrections → re-run the previous step incorporating their feedback
- Do NOT write a verdict file and auto-proceed — this gate requires human input

*On RELOOP: return to `strategy_director` (max 3 iterations)*

## Phase 8: Archivist Plan

```bash
factory agent archivist --task "Archive the approved research and strategy.
Read: .factory/strategy/current.md
Write output to: .factory/archive/plan.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

## Phase 9: Builder

```bash
factory agent builder --task "Implement the next phase from .factory/strategy/current.md. Read the CEO's plan approval at .factory/reviews/ceo-verdict-strategist.md. Read CLAUDE.md and factory.md if they exist. Implement exactly what the current phase describes. Run tests. Commit changes and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 1200
```

```bash
# Artifact verification: builder
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/builder-latest.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 500 ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md smaller than 500 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE 'commit' "$_f" && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md missing required sentinel (commit)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=builder" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: builder artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=builder" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### CEO Review — Build

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Read builder output. Check git log and diff. Does the work match the plan for this phase? If the Builder opened a PR, read it. REDIRECT if off-scope or missed key requirements.
4. Write verdict to `.factory/reviews/ceo-verdict-build.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

## Phase 10: Qa (Parallel)

Spawn 3 agents in parallel:

```bash
factory agent health_checker --task "Execute health_checker task for the project.
Read: .factory/reviews/builder-latest.md, .factory/strategy/current.md
Write output to: .factory/reviews/health-check.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent code_reviewer --task "Execute code_reviewer task for the project.
Read: .factory/reviews/builder-latest.md, .factory/strategy/current.md
Write output to: .factory/reviews/code-review.md" --project "$PROJECT_PATH" --timeout 900 &
```

```bash
factory agent ceo --task "You are the QA Director for this design session.

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
factory agent adversarial_tester --task "<approach.prompt>" --project $PROJECT_PATH
```

Each tester writes to `.factory/reviews/adversarial-<slug>-latest.md`.

After ALL testers complete, review quality:
- Each adversarial report exists and has substantive findings
- All acceptance criteria are covered by at least one tester
- No tester missed its assigned focus area
- Critical findings are actually reproducible (spot-check)

If a tester produced thin output, re-invoke it with a more specific prompt.

Write a brief QA summary to the end of qa-plan.json noting which
approaches completed and any quality issues.
Read: .factory/reviews/builder-latest.md, .factory/strategy/current.md, .factory/strategy/user-intent.md
Write output to: .factory/reviews/qa-plan.json" --project "$PROJECT_PATH" --timeout 3600 &
```

```bash
wait
```

**Important:** Run ALL commands above in a **single** Bash tool call with timeout set to at least 3600 seconds.

```bash
# Artifact verification: health_checker
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/health-check.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: health_checker: .factory/reviews/health-check.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: health_checker: .factory/reviews/health-check.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=health_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: health_checker artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=health_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: code_reviewer
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/code-review.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: code_reviewer: .factory/reviews/code-review.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: code_reviewer: .factory/reviews/code-review.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=code_reviewer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: code_reviewer artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=code_reviewer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: qa_director
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/qa-plan.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: qa_director: .factory/reviews/qa-plan.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: qa_director: .factory/reviews/qa-plan.json is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 20 ] && echo "VERIFY FAIL: qa_director: .factory/reviews/qa-plan.json smaller than 20 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=qa_director" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: qa_director artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=qa_director" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(post-barrier harness verification — DO NOT SKIP)*

## Barrier: Qa

Wait for all parallel agents to complete: `health_checker`, `code_reviewer`, `qa_director`

Read combined outputs: `.factory/reviews/code-review.md`, `.factory/reviews/health-check.md`, `.factory/reviews/qa-plan.json`

## Step: Synthesize Qa

Merges ALL adversarial-*-latest.md reports into one synthesized QA report. Uses glob pattern — works for any K testers. Findings caught by 2+ testers = HIGH confidence. Single-source findings surfaced but marked. Health checker and code reviewer reports included as pass-through.

```bash
python3 -c "from pathlib import Path; import re, glob; project = '$PROJECT_PATH'; reports = []; for p in sorted(Path(f'{project}/.factory/reviews').glob('adversarial-*-latest.md')):     slug = p.name.replace('-latest.md', '').replace('adversarial-', '');     reports.append((slug, p.read_text())); findings = {}; for tester_slug, text in reports:     for line in text.splitlines():         stripped = line.strip();         if stripped.startswith('- ') or stripped.startswith('* '):             key = re.sub(r'\s+', ' ', stripped[2:].strip().lower()[:80]);             findings.setdefault(key, []).append(tester_slug); high = [(k, v) for k, v in findings.items() if len(v) >= 2]; medium = [(k, v) for k, v in findings.items() if len(v) == 1]; out = ['# Synthesized QA Report\n']; hc = Path(f'{project}/.factory/reviews/health-check.md'); cr = Path(f'{project}/.factory/reviews/code-review.md'); out.append('## Health Check\n'); out.append(hc.read_text() if hc.exists() else '(not available)'); out.append('\n## Code Review\n'); out.append(cr.read_text() if cr.exists() else '(not available)'); out.append('\n## High-Confidence Adversarial Findings (caught by 2+ testers)\n'); [out.append(f'- {k} (testers: {v})') for k, v in high]; if not high: out.append('- (none)'); out.append('\n## Medium-Confidence Adversarial Findings (single tester)\n'); [out.append(f'- {k} (tester: {v[0]})') for k, v in medium]; if not medium: out.append('- (none)'); out.append('\n## Raw Adversarial Reports\n'); [out.append(f'### Tester: {slug}\n{text}\n') for slug, text in reports]; Path(f'{project}/.factory/reviews/qa-synthesized.md').write_text('\n'.join(out)); print(f'Synthesized {len(high)} high + {len(medium)} medium findings from {len(reports)} adversarial reports')"
```

### CEO Review — Qa

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-synthesized.md`, `.factory/strategy/user-intent.md`
3. Assess: You are the CEO reviewing QA results. This is the final gate before merge.

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
  - Fundamental design flaw that builder iterations cannot fix
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

### CEO Review — Doc Freshness

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-synthesized.md`
3. Assess: Check the PR diff for documentation freshness. If public APIs, CLI commands, configuration options, or architecture were changed or added, corresponding documentation (README.md, CLAUDE.md, docstrings, --help text, or doc/ files) MUST be updated. PROCEED if docs are current or no doc-worthy changes exist. RELOOP to builder if documentation is stale — specify exactly which changes need doc updates.
4. Write verdict to `.factory/reviews/ceo-verdict-doc-freshness.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

### Gate — Precheck (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `archivist_build`
- **HALT** (exit non-zero / FAIL in output) → continue to `archivist_build` instead.

## Phase 11: Archivist Build

```bash
factory agent archivist --task "Archive the build phase results.
Read: .factory/reviews/qa-synthesized.md
Write output to: .factory/archive/build.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

## Step: Spec Generate

Generate the project specification via the gated spec-generate workflow. Runs non-blocking after archival.

```bash
factory workflow run spec-generate $PROJECT_PATH
```
