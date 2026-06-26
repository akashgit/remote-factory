---
name: workflow-improve
description: "Improve an existing project through systematic experimentation. Runs study, research, hypothesis generation, build/eval loop, and archival. Use when the user says 'improve X', 'make X better', or the project state is has_factory."
disable-model-invocation: true
argument-hint: "<project_path> [--focus <target>]"
---

# Improve Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Observe


Run local study to gather observations:

```bash
factory study $PROJECT_PATH
```

Writes observations to `.factory/strategy/observations.md`.

## Phase 2: Researcher


```bash
factory agent researcher --task "Deep research for the project. Read observations at .factory/strategy/observations.md. Analyze codebase structure, eval scores, and experiment history. Search the web for best practices relevant to weak dimensions. Check .factory/archive/ for prior knowledge. Write findings to .factory/strategy/research-local.md.
Read: .factory/strategy/observations.md
Write output to: .factory/strategy/research-local.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research-local.md`
3. Assess: Are observations grounded in data? Did web research surface useful patterns? Any blind spots in the analysis?
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `researcher` (max 3 iterations)*

## Phase 3: Strategist


```bash
factory agent strategist --task "Generate prioritized hypotheses. Read the backlog at .factory/strategy/backlog.md — clear as many items as possible. Read Hypothesis Budget from observations for constraints. Read CEO research review at .factory/reviews/ceo-verdict-researcher.md. Each hypothesis must be specific, scoped to one PR, tied to observations, with expected impact on eval dimensions. Tag backlog items with **Backlog item:** and new items with **New:**. Write to .factory/strategy/current.md.
Read: .factory/strategy/observations.md, .factory/strategy/research-local.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Strategy

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/current.md`
3. Assess: HARD GATE. Check: specific enough to implement? Scoped to one PR? Expected eval impact realistic? Follows FEEC priority? Not redundant with reverted experiment? At least one growth hypothesis? Backlog convergence? Write PLAN APPROVED with approved hypotheses in priority order.
4. Write verdict to `.factory/reviews/ceo-verdict-strategy.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `strategist` (max 3 iterations)*

## Step: Begin


```bash
factory begin $PROJECT_PATH --hypothesis "Implement hypothesis"
```

## Phase 4: Builder


```bash
factory agent builder --task "Implement the current hypothesis from .factory/strategy/current.md. Read CLAUDE.md and factory.md. Read the CEO strategy approval. Implement exactly what the hypothesis describes. Run tests. Commit and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Build

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Read builder output and PR diff. Does work match the hypothesis? No scope creep? Tests included? REDIRECT if off-scope.
4. Write verdict to `.factory/reviews/ceo-verdict-build.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

## Step: Eval


```bash
factory eval "$PROJECT_PATH"
```

### Gate — Precheck (Automated)

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

## Step: Finalize


```bash
factory finalize $PROJECT_PATH --id 1 --verdict keep --hypothesis 'hypothesis'
```

## Phase 6: Archivist


```bash
factory agent archivist --task "Archive experiment results and learnings.
Read: .factory/experiments/verdict.json
Write output to: .factory/archive/experiment.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
