---
name: workflow-research
description: "Research mode — extends improve with baseline measurement, failure analysis, research-command eval, and plateau detection. Use when the project has research_target configured and the user says 'research X' or wants metric-driven optimization."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Research Workflow

The user wants: **$ARGUMENTS**

## Step: Baseline


```bash
factory eval "$PROJECT_PATH"
```

## Phase 1: Failure Analyst


```bash
factory agent failure_analyst --task "Analyze research run results. Read run artifacts at .factory/research/runs/. Read research target config from .factory/config.json. Classify failures by type and severity. Compute failure distribution. Suggest interventions within mutable surfaces only. Write to .factory/strategy/failure_analysis.md.
Read: .factory/experiments/baseline.json
Write output to: .factory/strategy/failure_analysis.md" --project "$PROJECT_PATH" --timeout 600
```

## Phase 2: Researcher


```bash
factory agent researcher --task "Failure-targeted research. Read failure analysis at .factory/strategy/failure_analysis.md. Search the web for solutions to the dominant failure modes. Check .factory/archive/ for prior knowledge on these patterns. Write findings to .factory/strategy/research-local.md.
Read: .factory/strategy/failure_analysis.md
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
factory agent strategist --task "Generate research hypotheses targeting dominant failure modes. Each hypothesis must improve over the previous baseline score. Each hypothesis must name specific files from mutable_surfaces to modify. Hypotheses MUST NOT modify files in fixed_surfaces. Prioritize by expected impact on the target metric. Write 1-3 hypotheses to .factory/strategy/current.md.
Read: .factory/strategy/failure_analysis.md, .factory/strategy/research-local.md
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

## Phase 5: Archivist


```bash
factory agent archivist --task "Archive experiment results and learnings.
Read: .factory/experiments/verdict.json
Write output to: .factory/archive/experiment.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

### Gate — Plateau Gate (Automated)

```bash
python3 -c "import json, pathlib, sys; tsv = pathlib.Path('$PROJECT_PATH/.factory/results.tsv'); lines = [l for l in tsv.read_text().strip().splitlines()[1:] if l.strip()] if tsv.exists() else []; scores = []; [scores.append(float(p)) for l in lines for i, p in enumerate(l.split(chr(9))) if i == 2 and p]; recent = scores[-3:] if len(scores) >= 3 else scores; improved = len(recent) < 2 or recent[-1] > recent[-2]; print('RELOOP' if improved else 'PROCEED')"
```

*On RELOOP: return to `baseline` (max 3 iterations)*
