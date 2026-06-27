---
name: workflow-create
description: "Create mode — meta-mode for creating new factory modes from user descriptions. Takes a description (text, spec file, or flow) and produces a fully working workflow definition, SKILL.md, CLI wiring, and tests. Use when the user says 'create a mode for X', 'add a new workflow', or wants to extend the factory with a custom pipeline."
disable-model-invocation: true
argument-hint: ""mode description" or /path/to/spec.md"
---

# Create Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Research (Parallel)


Spawn 3 agents in parallel:

```bash
factory agent researcher --review-tag existing --task "Existing workflow analysis. Read factory/workflow/definitions.py and analyze all existing workflow definitions (build, design, improve, research, meta, discover, review, refine). Document common patterns: node sequences, gate conventions, fork/join patterns, archivist placement, edge wiring, trigger functions, reads/writes declarations. Read factory/workflow/primitives.py for available node types and their fields. Read factory/workflow/skill_export.py for WORKFLOW_META format. Write findings to .factory/strategy/research-existing.md covering: node type usage patterns, common subgraphs (builder→gate→qa→gate loop), trigger function conventions, data flow patterns.
Write output to: .factory/strategy/research-existing.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag intent --task "Mode description analysis. Read the user's mode description from the CEO task. Parse and structure it into a workflow specification: - Purpose and trigger conditions - Agent roles needed (which specialists) - Gate logic (user vs agent vs fn evaluators) - Data flow (what files are read/written) - Interactive vs headless requirements - Input format (text, file, drawing, flow) Write findings to .factory/strategy/research-intent.md covering: structured requirements, node candidates, suggested graph topology.
Write output to: .factory/strategy/research-intent.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag practices --task "Workflow design best practices. Search the web for workflow and pipeline design patterns relevant to the described mode. Look for: DAG design patterns, agent orchestration patterns, quality gate strategies, error recovery approaches. Check .factory/archive/ for lessons from past mode creation or workflow changes. Write findings to .factory/strategy/research-practices.md covering: relevant design patterns, pitfalls to avoid, testing strategies.
Write output to: .factory/strategy/research-practices.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
wait
```

## Barrier: Research


Wait for all parallel agents to complete: `researcher_existing`, `researcher_intent`, `researcher_practices`

Read combined outputs: `.factory/strategy/research-existing.md`, `.factory/strategy/research-intent.md`, `.factory/strategy/research-practices.md`

Write combined result to: `.factory/strategy/research-combined.md`

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research-combined.md`
3. Assess: Are the existing workflow patterns well-documented? Is the user's intent clearly structured into workflow requirements? Are best practices relevant to this type of mode? Any gaps?
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `fork_research` (max 3 iterations)*

## Phase 2: Strategist


```bash
factory agent strategist --task "Synthesize a complete workflow specification for a new factory mode. Read ALL tagged research files at .factory/strategy/research-*.md. Produce a complete specification including: 1) Python code for the workflow function (nodes dict, edges list, trigger) 2) WORKFLOW_META entry (description, argument_hint) 3) CLI wiring changes (build_parser mode choices, cmd_ceo routing, _build_ceo_task section) 4) Test cases (graph validation, skill export, trigger function, registration) 5) Node details: for each node, specify id, type, role, prompt_template, reads, writes 6) Edge details: for each edge, specify source, target, condition 7) Interactive vs headless behavior Follow conventions from existing workflows — use the same patterns for builder→gate→QA→gate loops, archivist placement, and research forks. Write the specification to .factory/strategy/current.md.
Read: .factory/strategy/research-combined.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

### Steering Point — Strategy (User Approval)

Present findings to the user. Wait for approval or feedback.
- **Approve** → proceed to next step
- **Feedback** → re-run the previous step with corrections

*On RELOOP: return to `strategist` (max 3 iterations)*

## Phase 3: Archivist Plan


```bash
factory agent archivist --task "Archive the approved workflow specification for the new mode.
Read: .factory/strategy/current.md
Write output to: .factory/archive/create-plan.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

## Phase 4: Builder


```bash
factory agent builder --task "Implement the new factory mode from the approved workflow specification. Read the approved spec at .factory/strategy/current.md. Read CLAUDE.md for project conventions. Implementation checklist: 1) Add the workflow function to factory/workflow/definitions.py 2) Register it in register_all() 3) Add WORKFLOW_META entry in factory/workflow/skill_export.py 4) Wire --mode in factory/cli.py (build_parser, cmd_ceo, _build_ceo_task) 5) Run factory workflow validate <name> to verify the graph 6) Run factory workflow export-skills to generate the SKILL.md 7) Write tests in tests/ 8) Run pytest and ruff check to verify Commit changes and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Build

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Read builder output and PR diff. Does work match the approved spec? Verify: workflow function exists, registered in register_all(), WORKFLOW_META entry added, CLI wiring complete, tests written. REDIRECT if any component is missing.
4. Write verdict to `.factory/reviews/ceo-verdict-build.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

## Phase 5: Qa


```bash
factory agent qa --task "Verify the new factory mode end-to-end. 1. Health Check — run pytest, ruff check, mypy. Report results. 2. Code Review — read PR diff, evaluate correctness, architecture, edge cases, security. Verify workflow graph validates. 3. Adversarial QA — actually test the new mode:    - Run: factory workflow validate <name>    - Run: factory workflow show <name>    - Run: factory workflow export-skills --verify    - Verify SKILL.md was generated under skills/workflow-<name>/    - Check CLI recognizes --mode <name> (factory ceo --help)    - Check the workflow handles both interactive and headless paths Write results to .factory/reviews/qa-latest.md
Read: .factory/reviews/builder-latest.md
Write output to: .factory/reviews/qa-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Qa

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-latest.md`
3. Assess: Review QA results for the new mode. PROCEED if all checks pass: workflow validates, SKILL.md generated, tests pass, CLI recognizes mode. RELOOP to builder (max 3 iterations) if issues found.
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

### Gate — Precheck (Automated)

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

## Phase 6: Archivist Build


```bash
factory agent archivist --task "Archive the new mode build results and learnings.
Read: .factory/reviews/qa-latest.md
Write output to: .factory/archive/create-build.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
